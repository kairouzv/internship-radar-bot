"""Décide si une offre mérite une alerte : stage ? bon lieu ? bon métier ? bonnes dates ?"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .extract import DateVerdict, Extraction, evaluate_dates, extract
from .models import Job
from .textutil import norm

TIERS = ("TOP", "GOOD", "CHECK", "DIGEST", "DROP")

# ── Contrat ─────────────────────────────────────────────────────────────────────────────────
INTERN_RE = re.compile(
    r"\b(?:intern|interns|internship|internships|stage|stages|stagiaire|stagiaires|praktikum|praktikant|"
    r"praktikantin|summer analyst|spring analyst|off-cycle|offcycle|off cycle|spring week|placement year|"
    r"industrial placement|working student|werkstudent)\b"
)
ALT_RE = re.compile(
    r"\b(?:alternance|alternant|alternante|apprenti|apprentie|apprentissage|apprenticeship|apprentice|"
    r"contrat pro|contrat de professionnalisation|work-study|work study|dual study|duales studium)\b"
)
VIE_RE = re.compile(r"\bV\.?I\.?E\.?\b|volontariat international", re.I)
GRAD_RE = re.compile(r"\b(?:graduate program\w*|graduate scheme|programme graduate|graduate analyst|jeune diplome|young graduate)\b")
_FALSE_STAGE = re.compile(r"\b(?:early|late|later|seed|growth|mid|pre|series [a-d])[- ]stage\b")
PERM_RE = re.compile(r"\b(?:cdi|cdd|permanent|full[- ]time|temps plein|freelance|interim)\b")


# ── Lieu ────────────────────────────────────────────────────────────────────────────────────
def _boundary(patterns: list[str]) -> re.Pattern:
    alts = "|".join(sorted((re.escape(norm(p)) for p in patterns), key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])(?:{alts})(?![a-z0-9])") if alts else re.compile(r"(?!x)x")


@dataclass
class Verdict:
    tier: str = "DROP"
    score: int = 0
    desks: list[str] = field(default_factory=list)
    internship: bool = False
    location_ok: bool | None = None
    dates: DateVerdict | None = None
    extraction: Extraction | None = None
    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    @property
    def dates_status(self) -> str:
        return self.dates.status if self.dates else "UNKNOWN"


class Classifier:
    def __init__(self, profile: dict, today: date | None = None):
        self.profile = profile
        self.today = today or date.today()
        self.window = profile["window"]
        self.contract = profile.get("contract", {})
        loc = profile.get("locations", {})
        self.loc_inc = _boundary(loc.get("include", []))
        self.loc_exc = _boundary(loc.get("exclude", []))
        self.desks = [
            (d["name"], float(d["weight"]), _boundary(d["patterns"])) for d in profile.get("desks", [])
        ]
        self.neg = _boundary(profile.get("exclude_title", []))
        th = profile.get("thresholds", {})
        self.t_notify = float(th.get("notify", 60))
        self.t_top = float(th.get("top", 85))
        self.t_digest = float(th.get("digest", 35))

    # ── contrat ─────────────────────────────────────────────────────────────────────────────
    def check_contract(self, job: Job) -> tuple[bool, list[str], list[str]]:
        head = _FALSE_STAGE.sub(" ", norm(f"{job.title} {job.contract}"))
        flags: list[str] = []
        why: list[str] = []
        has_intern = bool(INTERN_RE.search(head))
        has_alt = bool(ALT_RE.search(head))
        if VIE_RE.search(f"{job.title} {job.contract}") and not self.contract.get("accept_vie", False):
            return False, ["VIE exclu"], flags
        if GRAD_RE.search(head) and not has_intern and not self.contract.get("accept_graduate_programs", False):
            return False, ["programme graduate (pas un stage)"], flags
        if has_alt and not has_intern and not self.contract.get("accept_alternance", False):
            return False, ["alternance exclue"], flags
        if has_alt and has_intern:
            flags.append("alternance_possible")
            why.append("stage ou alternance")
        if has_intern:
            return True, why or ["type stage détecté"], flags
        if job.extra.get("trust_internship"):
            return True, ["source déjà filtrée sur les stages"], flags
        if PERM_RE.search(head):
            return False, ["contrat permanent/temps plein"], flags
        return False, ["aucun indice de stage"], flags

    # ── lieu ────────────────────────────────────────────────────────────────────────────────
    def check_location(self, job: Job) -> bool | None:
        loc = norm(job.location)
        title = norm(job.title)
        if loc and self.loc_exc.search(loc):
            return False
        if self.loc_inc.search(loc) or self.loc_inc.search(title):
            return True
        if not loc:
            return None
        return False

    # ── métier ──────────────────────────────────────────────────────────────────────────────
    def score_role(self, job: Job) -> tuple[int, list[str], list[str]]:
        head = norm(f"{job.title} {job.department}")
        body = norm(job.description[:1500])
        best, names = 0.0, []
        for name, weight, rx in self.desks:
            in_head = bool(rx.search(head))
            in_body = bool(rx.search(body))
            val = weight if in_head else (min(weight * 0.7, 70) if in_body else 0)
            if val > 0:
                names.append((val, name))
            best = max(best, val)
        names.sort(reverse=True)
        score = best
        if sum(1 for v, _ in names if v >= 70) >= 2:
            score = min(100, score + 5)
        reasons: list[str] = []
        if self.neg.search(head):
            score = max(0, score - 60)
            reasons.append("titre hors cible (mot exclu)")
        return int(round(score)), [n for _, n in names[:3]], reasons

    # ── global ──────────────────────────────────────────────────────────────────────────────
    def classify(self, job: Job) -> Verdict:
        v = Verdict()
        ok, why, flags = self.check_contract(job)
        v.internship = ok
        v.flags.extend(flags)
        v.reasons.extend(why)
        if not ok:
            v.tier = "DROP"
            return v

        v.location_ok = self.check_location(job)
        if v.location_ok is False:
            v.reasons.append(f"lieu hors cible ({job.location or '?'})")
            v.tier = "DROP"
            return v
        if v.location_ok is None:
            v.flags.append("location_unknown")
            v.reasons.append("lieu non précisé")

        v.score, v.desks, r = self.score_role(job)
        v.reasons.extend(r)
        if v.score < self.t_digest:
            v.reasons.append(f"métier peu pertinent (score {v.score})")
            v.tier = "DROP"
            return v
        v.reasons.append(f"métier {', '.join(v.desks) or '—'} (score {v.score})")

        v.extraction = extract(job.title, job.description, job.start_hint, ref=self.today)
        v.dates = evaluate_dates(v.extraction, self.window)
        v.reasons.extend(v.dates.reasons)
        v.flags.extend(f for f in v.dates.flags if f not in v.flags)

        v.tier = self._tier(v)
        return v

    def _tier(self, v: Verdict) -> str:
        notifiable = v.score >= self.t_notify
        status = v.dates_status
        win = self.window
        if status == "FAIL":
            return "DIGEST" if notifiable else "DROP"
        if status == "ASAP":
            pol = win.get("asap_policy", "digest")
            return {"notify": "CHECK", "digest": "DIGEST", "reject": "DROP"}.get(pol, "DIGEST") if notifiable else "DROP"
        if not notifiable:
            return "DIGEST"
        if status == "PASS":
            return "TOP" if v.score >= self.t_top else "GOOD"
        pol = win.get("unknown_dates_policy", "notify")
        return {"notify": "CHECK", "digest": "DIGEST", "reject": "DROP"}.get(pol, "CHECK")
