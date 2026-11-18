"""Dédoublonnage « flou » inter-sources.

Une même offre arrive souvent par plusieurs canaux (page carrières, LinkedIn, Indeed, WTTJ…) avec un titre
ou un nom d'entreprise écrit un peu différemment. L'empreinte exacte (Job.fingerprint) ne les relie pas ;
ici on compare : entreprise (clé normalisée) + ville + ressemblance des titres (jetons canoniques).
Volontairement prudent : deux offres de desks, de saisons ou d'années différents ne sont jamais fusionnées.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .models import Job
from .textutil import norm, norm_company, norm_title

# ── ville ───────────────────────────────────────────────────────────────────────────────────

_CITY_ALIASES = {"geneva": "geneve", "genf": "geneve", "geneve": "geneve", "ladefense": "paris"}


def canon_city(location: str | None) -> str:
    city = norm(location).split(",")[0].split(" - ")[0].strip()
    city = "".join(ch for ch in city if ch.isalnum())
    if city.startswith("paris"):
        return "paris"
    return _CITY_ALIASES.get(city, city)


# ── titre ───────────────────────────────────────────────────────────────────────────────────

# expressions équivalentes -> un seul jeton (desks). Appliquées sur le titre normalisé (minuscules, sans accents).
_PHRASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bm ?& ?a\b|\bmergers? (?:and |& )?acquisitions?\b|\bfusions? (?:et |& )?acquisitions?\b|\bfusacs?\b"), " ma "),
    (re.compile(r"\bleveraged finance\b|\blev ?fin\b|\bfinancements? (?:a effet de levier|lbo)\b|\blbo finance\b"), " levfin "),
    (re.compile(r"\bprivate equity\b|\bcapital[- ]investissement\b"), " pe "),
    (re.compile(r"\bventure capital\b|\bcapital[- ]risque\b"), " vc "),
    (re.compile(r"\binvestor relations?\b|\brelations? investisseurs?\b"), " ir "),
    (re.compile(r"\binvestment banking\b|\bbanque d investissement\b"), " ib "),
    (re.compile(r"\bcapital markets?\b|\bmarches? de capitaux\b"), " cm "),
    (re.compile(r"\bglobal markets?\b"), " gm "),
    (re.compile(r"\basset management\b|\bgestion d actifs\b"), " am "),
]

_DESKS = {"ma", "levfin", "pe", "vc", "ir", "ib", "cm", "gm", "am"}
_SEASONS = {"summer": "summer", "ete": "summer", "spring": "spring", "printemps": "spring",
            "autumn": "autumn", "fall": "autumn", "automne": "autumn", "winter": "winter", "hiver": "winter"}
_YEAR = re.compile(r"^20\d\d$")
# mots sans valeur discriminante (type de contrat, genre, lieu, articles, ponctuation résiduelle)
_NOISE = {
    "stage", "stagiaire", "stagiaires", "intern", "interns", "internship", "internships", "stages", "trainee", "traineeship",
    "alternance", "apprenti", "apprentice", "apprenticeship", "vie", "v", "h", "f", "m", "w", "d", "x", "mfd", "mwd", "hf",
    "paris", "geneva", "geneve", "geneve", "france", "suisse", "switzerland", "ile", "de", "la", "le", "les", "l", "du", "des",
    "d", "en", "dans", "au", "aux", "a", "an", "the", "of", "in", "at", "for", "pour", "et", "and", "to", "on", "with", "avec",
    "program", "programme", "programmes", "programs", "mois", "months", "month", "cdd", "temps", "plein", "full", "time",
    "junior", "new", "nouveau",
}


def title_tokens(title: str | None) -> frozenset[str]:
    t = norm_title(title)
    t = t.replace("&amp;", "&")
    for rx, rep in _PHRASES:
        t = rx.sub(rep, t)
    toks = {_SEASONS.get(w, w) for w in re.split(r"[^a-z0-9]+", t) if w}
    return frozenset(w for w in toks if w not in _NOISE and (len(w) > 1 or w in _DESKS or w.isdigit()))


def _discriminants(toks: frozenset[str]) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    desks = frozenset(w for w in toks if w in _DESKS)
    seasons = frozenset(w for w in toks if w in _SEASONS.values())
    years = frozenset(w for w in toks if _YEAR.match(w))
    return desks, seasons, years


def similar_titles(a: frozenset[str], b: frozenset[str], *, strict: bool = False) -> bool:
    """Deux jeux de jetons décrivent-ils la même offre ? strict : égalité exigée (localisation inconnue)."""
    if not a or not b:
        return False
    if a == b:
        return True
    if strict:
        return False
    # desk / saison / année : s'ils sont renseignés des deux côtés, l'un doit contenir l'autre (sinon offres distinctes)
    for x, y in zip(_discriminants(a), _discriminants(b)):
        if x and y and not (x <= y or y <= x):
            return False
    inter = len(a & b)
    union = len(a | b)
    if inter / union >= 0.75:
        return True
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    # un titre est le sous-ensemble de l'autre (ex. « M&A Analyst » vs « Investment Banking M&A Analyst »)
    return small <= big and (len(small) >= 2 or bool(small & _DESKS)) and len(big) - len(small) <= 2


# ── entreprise ──────────────────────────────────────────────────────────────────────────────

def same_company(a: str, b: str) -> bool:
    """Clés normalisées égales, ou l'une préfixe de l'autre (« bnpparibas » / « bnpparibascib »)."""
    if not a or not b:
        return False
    if a == b:
        return True
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    return len(small) >= 5 and big.startswith(small)


# ── index ───────────────────────────────────────────────────────────────────────────────────

class DupIndex:
    """Offres déjà vues (base + passage en cours), interrogeables par entreprise."""

    def __init__(self) -> None:
        self._by_company: dict[str, list[tuple[str, frozenset[str], str]]] = {}

    def add_raw(self, company: str | None, title: str | None, location: str | None) -> None:
        ck = norm_company(company)
        if not ck:
            return
        self._by_company.setdefault(ck, []).append((canon_city(location), title_tokens(title), ck))

    def add(self, job: Job) -> None:
        self.add_raw(job.company, job.title, job.location)

    def match(self, job: Job) -> bool:
        ck = norm_company(job.company)
        if not ck:
            return False
        city, toks = canon_city(job.location), title_tokens(job.title)
        for key, rows in self._by_company.items():
            if not same_company(ck, key):
                continue
            for c, t, _ in rows:
                if city and c and city != c:
                    continue
                if similar_titles(toks, t, strict=not (city and c)):
                    return True
        return False

    @classmethod
    def from_store(cls, store, days: int = 120) -> "DupIndex":
        """Charge les offres déjà montrées à l'utilisateur (alertées ou en résumé) ; les rejetées ne bloquent rien."""
        idx = cls()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = store.db.execute(
            "SELECT company, title, location FROM jobs WHERE first_seen>=? AND tier NOT IN ('DROP','DUP')", (cutoff,)
        ).fetchall()
        for r in rows:
            idx.add_raw(r[0], r[1], r[2])
        return idx
