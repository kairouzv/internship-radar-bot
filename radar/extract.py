"""Extraction des dates de début et de la durée d'un stage (FR / EN / DE).

Les offres n'ont presque jamais de champ « date de début » structuré : l'information est dans le
texte (« starting June 2027 », « stage de 6 mois à partir de septembre », « Q2 2027 »...).
Ce module la retrouve et la compare à la fenêtre de stage définie dans le profil.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

from .textutil import norm_lines

FR_MONTHS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

_MONTHS: dict[str, int] = {}
for _m, _names in enumerate(
    [
        ["janvier", "janv", "january", "januar", "jan"],
        ["fevrier", "fevr", "fev", "february", "februar", "feb"],
        ["mars", "march", "marz", "mar"],
        ["avril", "april", "avr", "apr"],
        ["mai", "may"],
        ["juin", "june", "juni", "jun"],
        ["juillet", "juil", "july", "juli", "jul"],
        ["aout", "august", "aug"],
        ["septembre", "september", "sept", "sep"],
        ["octobre", "october", "oktober", "oct"],
        ["novembre", "november", "nov"],
        ["decembre", "december", "dezember", "dec"],
    ],
    start=1,
):
    for _n in _names:
        _MONTHS[_n] = _m

# Mois ambigus (verbes/mots courants) : acceptés seulement avec un jour ou une année.
_AMBIGUOUS_ALONE = {"may", "mar", "march"}


def _alt(words) -> str:
    return "|".join(sorted((re.escape(w) for w in words), key=len, reverse=True))


MONTH = rf"(?:{_alt(_MONTHS)})\b\.?"
YEAR = r"(20[2-3]\d)"
PREFIX = (
    r"(?:\b(early|beginning of|start of|debut d'|debut de|debut|mid|mi|milieu de|milieu|late|end of|"
    r"fin d'|fin de|fin|courant)\s*-?\s*)?"
)

SEASONS = {
    "spring": (3, 1, 5, 31), "printemps": (3, 1, 5, 31), "fruhling": (3, 1, 5, 31),
    "summer": (6, 1, 8, 31), "ete": (6, 1, 8, 31), "sommer": (6, 1, 8, 31),
    "autumn": (9, 1, 11, 30), "fall": (9, 1, 11, 30), "automne": (9, 1, 11, 30), "herbst": (9, 1, 11, 30),
    "winter": (1, 1, 2, 28), "hiver": (1, 1, 2, 28),
}
_SEASON_RE = _alt(SEASONS)

ORDINALS = {
    "1er": 1, "1st": 1, "first": 1, "premier": 1, "1": 1, "1re": 1, "1ere": 1,
    "2nd": 2, "second": 2, "2eme": 2, "2e": 2, "deuxieme": 2, "2": 2, "2d": 2,
    "3eme": 3, "3rd": 3, "third": 3, "troisieme": 3, "3": 3, "3e": 3,
    "4eme": 4, "4th": 4, "fourth": 4, "quatrieme": 4, "4": 4, "4e": 4,
}
_ORD_RE = _alt(ORDINALS)


@dataclass
class Mention:
    kind: str                       # exact | month | season | quarter | half | year | asap
    span: tuple[int, int]
    start: date | None
    end: date | None
    label: str
    year_inferred: bool = False
    role: str = "unknown"           # start | end | unknown
    weak: bool = False              # aucun mot-clé « début » à proximité (titre, champ source)
    rebuild: Callable[[int], "Mention"] | None = field(default=None, repr=False, compare=False)


@dataclass
class Duration:
    min_months: float
    max_months: float
    label: str
    implied: bool = False           # déduite d'une plage « de janvier à juin »


@dataclass
class Extraction:
    starts: list[Mention] = field(default_factory=list)
    ends: list[Mention] = field(default_factory=list)
    asap: bool = False
    duration: Duration | None = None


# ---------------------------------------------------------------------------------------------
# Construction des mentions
# ---------------------------------------------------------------------------------------------

def _infer_year(month: int, ref: date) -> int:
    floor = ref - timedelta(days=30)
    for y in (ref.year, ref.year + 1, ref.year + 2):
        if date(y, month, calendar.monthrange(y, month)[1]) >= floor:
            return y
    return ref.year + 2


def _qual(token: str | None) -> str | None:
    if not token:
        return None
    t = token.strip()
    if t.startswith(("early", "beginning", "start", "debut")):
        return "early"
    if t.startswith(("mid", "mi", "milieu")):
        return "mid"
    if t.startswith(("late", "end", "fin")):
        return "late"
    return None


_QUAL_LABEL = {"early": "début ", "mid": "mi-", "late": "fin "}


def _month_mention(span, month: int, year: int | None, day: int | None, qual: str | None, ref: date) -> Mention:
    def build(y: int, inferred: bool) -> Mention:
        last = calendar.monthrange(y, month)[1]
        if day:
            d = date(y, month, min(day, last))
            m = Mention("exact", span, d, d, f"{day} {FR_MONTHS[month - 1]} {y}", inferred)
        else:
            lo, hi = {"early": (1, 10), "mid": (11, 20), "late": (21, last)}.get(qual or "", (1, last))
            label = f"{_QUAL_LABEL.get(qual or '', '')}{FR_MONTHS[month - 1]} {y}"
            m = Mention("month", span, date(y, month, lo), date(y, month, hi), label, inferred)
        m.rebuild = lambda yy, b=build: b(yy, False)
        return m

    if year is None:
        return build(_infer_year(month, ref), True)
    return build(year, False)


def _range_mention(kind, span, y, m1, d1, m2, d2, label, inferred=False) -> Mention:
    return Mention(kind, span, date(y, m1, d1), date(y, m2, d2), label, inferred)


def _scan(text: str, ref: date) -> list[Mention]:
    """Trouve toutes les expressions de date dans un texte normalisé (minuscules, sans accents)."""
    found: list[Mention] = []

    # ISO 2027-06-01
    for m in re.finditer(r"\b(20[2-3]\d)-(0?[1-9]|1[0-2])-(0?[1-9]|[12]\d|3[01])\b", text):
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
        found.append(_month_mention(m.span(), mo, y, d, None, ref))

    # 01/06/2027 ou 01.06.2027 (jour/mois/année, usage FR/CH)
    for m in re.finditer(r"\b(0?[1-9]|[12]\d|3[01])[/.](0?[1-9]|1[0-2])[/.](20[2-3]\d)\b", text):
        found.append(_month_mention(m.span(), int(m[2]), int(m[3]), int(m[1]), None, ref))

    # 06/2027
    for m in re.finditer(r"(?<![\d/.])(0?[1-9]|1[0-2])/(20[2-3]\d)\b", text):
        found.append(_month_mention(m.span(), int(m[1]), int(m[2]), None, None, ref))

    # 1er juin 2027 / 1 june / 15th of may 2027
    for m in re.finditer(
        rf"\b(\d{{1,2}})(?:er|st|nd|rd|th|e)?\s+(?:of\s+|de\s+|d')?({MONTH})(?:\s*,?\s*{YEAR}\b)?", text
    ):
        day = int(m[1])
        if 1 <= day <= 31:
            found.append(_month_mention(m.span(), _MONTHS[m[2].rstrip(".")], int(m[3]) if m[3] else None, day, None, ref))

    # june 1st, 2027 / june 1 2027
    for m in re.finditer(rf"\b({MONTH})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:\s*,\s*|\s+){YEAR}\b", text):
        day = int(m[2])
        if 1 <= day <= 31:
            found.append(_month_mention(m.span(), _MONTHS[m[1].rstrip(".")], int(m[3]), day, None, ref))

    # (début|mid|fin) juin 2027
    for m in re.finditer(rf"{PREFIX}({MONTH})(?:\s+|\s*,\s*|\s*-\s*|\s+of\s+|\s+de\s+|\s+d')\s*{YEAR}\b", text):
        found.append(_month_mention(m.span(), _MONTHS[m[2].rstrip(".")], int(m[3]), None, _qual(m[1]), ref))

    # mois seul (année déduite) — rôle vérifié plus tard par mot-clé
    for m in re.finditer(rf"{PREFIX}({MONTH})(?![\w])", text):
        tok = m[2].rstrip(".")
        if tok in _AMBIGUOUS_ALONE:
            continue
        found.append(_month_mention(m.span(), _MONTHS[tok], None, None, _qual(m[1]), ref))

    # summer 2027 / ete 2027 / summer internship 2027
    for m in re.finditer(rf"\b({_SEASON_RE})\b(?:\s+|[^.\n]{{0,25}}?\s+){YEAR}\b", text):
        mo1, d1, mo2, d2 = SEASONS[m[1]]
        y = int(m[2])
        found.append(_range_mention("season", m.span(), y, mo1, d1, mo2, d2, f"{m[1]} {y}".replace("ete", "été")))
    # summer internship / summer analyst (année déduite)
    for m in re.finditer(
        rf"\b({_SEASON_RE})\b\s+(?:internship|intern|analyst|placement|program(?:me)?|associate|stage|stagiaire)\b", text
    ):
        mo1, d1, mo2, d2 = SEASONS[m[1]]
        y = _infer_year(mo1, ref)
        mm = _range_mention("season", m.span(), y, mo1, d1, mo2, d2, f"{m[1]} {y}", inferred=True)
        found.append(mm)
    for m in re.finditer(rf"\bstage d'(ete|automne|hiver)\b|\bstage de (printemps)\b", text):
        tok = m[1] or m[2]
        mo1, d1, mo2, d2 = SEASONS[tok]
        y = _infer_year(mo1, ref)
        found.append(_range_mention("season", m.span(), y, mo1, d1, mo2, d2, f"{tok.replace('ete', 'été')} {y}", inferred=True))

    # Q2 2027 / 2027 Q2
    for m in re.finditer(rf"\bq([1-4])\s*[' ]?\s*{YEAR}\b|\b{YEAR}\s*q([1-4])\b", text):
        q = int(m[1] or m[4])
        y = int(m[2] or m[3])
        found.append(_range_mention("quarter", m.span(), y, 3 * q - 2, 1, 3 * q, calendar.monthrange(y, 3 * q)[1], f"T{q} {y}"))
    # 2e trimestre 2027 / first quarter of 2027
    for m in re.finditer(rf"\b({_ORD_RE})\s+(?:trimestre|quarter)\s+(?:of\s+|de\s+)?{YEAR}\b", text):
        q = ORDINALS[m[1]]
        y = int(m[2])
        found.append(_range_mention("quarter", m.span(), y, 3 * q - 2, 1, 3 * q, calendar.monthrange(y, 3 * q)[1], f"T{q} {y}"))
    # H2 2027 / S2 2027 / 1er semestre 2027 / second half of 2027
    for m in re.finditer(
        rf"\b[hs]([12])\s*{YEAR}\b|\b({_ORD_RE})\s+(?:semestre|half)\s+(?:of\s+|de\s+)?{YEAR}\b", text
    ):
        h = int(m[1]) if m[1] else ORDINALS[m[3]]
        y = int(m[2] or m[4])
        if h in (1, 2):
            a, b = (1, 6) if h == 1 else (7, 12)
            found.append(_range_mention("half", m.span(), y, a, 1, b, calendar.monthrange(y, b)[1], f"S{h} {y}"))

    # early/mid/late 2027, début 2027, fin d'année 2027
    for m in re.finditer(
        rf"\b(early|mid|late|debut|mi|milieu|fin)\s*-?\s*(?:d'|de\s+|of\s+)?(?:l'annee\s+|annee\s+)?{YEAR}\b", text
    ):
        q = _qual(m[1])
        y = int(m[2])
        a, b = {"early": (1, 4), "mid": (5, 8), "late": (9, 12)}[q or "mid"]
        lab = {"early": "début", "mid": "milieu", "late": "fin"}[q or "mid"]
        found.append(_range_mention("year", m.span(), y, a, 1, b, calendar.monthrange(y, b)[1], f"{lab} {y}"))

    # année seule (accepté uniquement avec mot-clé « début » juste avant)
    for m in re.finditer(rf"\b{YEAR}\b", text):
        y = int(m[1])
        found.append(Mention("year", m.span(), date(y, 1, 1), date(y, 12, 31), str(y)))

    return _resolve_overlaps(found)


def _resolve_overlaps(found: list[Mention]) -> list[Mention]:
    # plus longues d'abord, puis première déclarée (ordre de priorité des patterns)
    order = sorted(range(len(found)), key=lambda i: (-(found[i].span[1] - found[i].span[0]), i))
    kept: list[Mention] = []
    for i in order:
        a, b = found[i].span
        if any(not (b <= k.span[0] or a >= k.span[1]) for k in kept):
            continue
        kept.append(found[i])
    return sorted(kept, key=lambda m: m.span[0])


# ---------------------------------------------------------------------------------------------
# Rôle d'une date : début, fin, ou autre
# ---------------------------------------------------------------------------------------------

_BOUNDARY = re.compile(r"(?:\.\s|\n|;|•|\||\?|!)")
_STRONG_START = re.compile(
    r"\b(?:start(?:s|ing|ed)?(?: date)?|begin(?:s|ning)?|commenc\w*|join(?:ing)?|intake|availability|available from|"
    r"earliest|target(?:ed)? start|expected start|planned start|date de debut|debut du stage|debut de stage|"
    r"debut|demarrage|demarre|a partir (?:du|de|d')|a compter (?:du|de|d')|a pourvoir (?:des|en|a partir)|prise de poste|prise de fonction|rentree|entree en fonction|"
    r"date d'embauche|date d'entree|integration|beginn|eintritt\w*|starttermin|ab sofort)\b"
)
_WEAK_START = re.compile(r"\b(?:from|as of|des|ab|available|planned|expected|pour|en)\b")
_END = re.compile(
    r"\b(?:until|till|through|ending|ends?|end date|jusqu'(?:a|au|en)|jusqu a|fin du stage|date de fin|bis|to)\b"
)
_PAIR_KW = re.compile(
    r"\b(?:from|du|de|between|entre|ab|vom|internship|stage|period\w*|duration|duree|dauer|placement)\b"
)
_CONNECT = re.compile(r"^\s*(?:-|/|to|until|till|through|a|au|jusqu'a|jusqu'au|et|and|bis)\s*$")


def _role_by_keyword(text: str, m: Mention) -> str:
    i = m.span[0]
    pre = text[max(0, i - 70): i]
    cut = list(_BOUNDARY.finditer(pre))
    if cut:
        pre = pre[cut[-1].end():]
    best_pos, best_role = -1, "unknown"
    for rx, role, limit in ((_STRONG_START, "start", 60), (_WEAK_START, "start", 22), (_END, "end", 22)):
        for k in rx.finditer(pre):
            if len(pre) - k.end() <= limit and k.end() > best_pos:
                # « to » dans « from X to Y » : c'est une fin
                best_pos, best_role = k.end(), role
    return best_role


def _assign_roles(text: str, mentions: list[Mention], default_role: str | None) -> None:
    for m in mentions:
        if m.kind == "year" and m.label.isdigit():
            r = _role_by_keyword(text, m)
            m.role = r if r == "start" else "unknown"   # année nue : début explicite uniquement
            continue
        m.role = _role_by_keyword(text, m)
        if m.role == "unknown" and default_role:
            m.role = default_role
            m.weak = True
    # plages « de janvier à juin 2027 »
    for a, b in zip(mentions, mentions[1:]):
        between = text[a.span[1]: b.span[0]]
        if len(between) <= 18 and _CONNECT.match(between):
            pre = text[max(0, a.span[0] - 45): a.span[0]]
            if a.role == "start" or _PAIR_KW.search(pre):
                a.role = "start"
                b.role = "end"
                if a.year_inferred and not b.year_inferred and a.rebuild is not None:
                    y = b.start.year if b.start else None
                    if y:
                        na = a.rebuild(y)
                        if na.start and b.end and na.start > b.end:
                            na = a.rebuild(y - 1)
                        a.start, a.end, a.label, a.year_inferred = na.start, na.end, na.label, False
                        a.rebuild = na.rebuild


# ---------------------------------------------------------------------------------------------
# Durée
# ---------------------------------------------------------------------------------------------

_NUMWORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4,
    "cinq": 5, "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "drei": 3,
    "vier": 4, "funf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "zwolf": 12,
}
_NUM = rf"(?:\d{{1,2}}(?:[.,]5)?|{_alt(_NUMWORDS)})"
_UNIT_M = r"(?:months?|mois|monate?n?)"
_UNIT_W = r"(?:weeks?|semaines?|wochen?)"
_UNIT_Y = r"(?:years?|ans?|annees?|jahre?)"
_DUR_CTX = re.compile(
    r"\b(?:intern\w*|stage\w*|stagiaire\w*|placement|duration|duree|dauer|period\w*|contract|contrat|term|"
    r"praktik\w*|mission|for|pour|pendant|d'une|durant|length|long)\b"
)
_DUR_STRONG = re.compile(r"\b(?:duration|duree|dauer|length|period\w*|internship|stage|intern)\b")
_DUR_SKIP_PRE = re.compile(
    r"(?:extend\w*|renew\w*|prolong\w*|reconduct\w*|probation|periode d'essai|essai|after|apres|within|experiences?|"
    r"over the (?:last|past)|plus de|more than|at least|minimum|min\.?)\s*(?:of\s+|de\s+|d')?\s*$"
)
_DUR_SKIP_POST = re.compile(r"^\W*(?:\w+\W+){0,3}?(?:experience|d'experience|exp\.|seniority|tenure|probation|essai|trial)")


def _num(tok: str) -> float:
    tok = tok.replace(",", ".")
    return float(tok) if re.fullmatch(r"\d+(?:\.\d)?", tok) else float(_NUMWORDS[tok])


def _to_months(value: float, unit: str) -> float:
    if re.match(r"(?:week|semaine|woche)", unit):
        return value / 4.345
    if re.match(r"(?:year|an|annee|jahr)", unit):
        return value * 12
    return value


def extract_duration(text: str) -> Duration | None:
    cands: list[tuple[int, int, Duration]] = []
    patterns = [
        rf"\b({_NUM})\s*(?:-|to|a|et|and|/|bis|or|ou)\s*({_NUM})\s*-?\s*({_UNIT_M}|{_UNIT_W})\b",
        rf"\b({_NUM})\s*-?\s*({_UNIT_M}|{_UNIT_W}|{_UNIT_Y})\b",
    ]
    for pi, rx in enumerate(patterns):
        for m in re.finditer(rx, text):
            a, b = m.span()
            pre, post = text[max(0, a - 45): a], text[b: b + 40]
            cut = _BOUNDARY.search(post)
            if cut:
                post = post[: cut.start()]
            cuts = list(_BOUNDARY.finditer(pre))
            if cuts:
                pre = pre[cuts[-1].end():]
            ctx = text[max(0, a - 70): b + 70]
            if _DUR_SKIP_PRE.search(pre) or _DUR_SKIP_POST.search(post):
                continue
            if not _DUR_CTX.search(ctx):
                continue
            if pi == 0:
                unit = m[3]
                lo, hi = _to_months(_num(m[1]), unit), _to_months(_num(m[2]), unit)
                if lo > hi:
                    lo, hi = hi, lo
                lab = f"{lo:g}-{hi:g} mois"
            else:
                unit = m[2]
                lo = hi = _to_months(_num(m[1]), unit)
                lab = f"{lo:.0f} mois" if abs(lo - round(lo)) < 0.05 else f"{lo:.1f} mois"
                if re.match(r"(?:year|an|annee|jahr)", unit) and not _DUR_STRONG.search(ctx):
                    continue
            if hi > 36 or hi <= 0:
                continue
            score = (3 if re.search(r"\b(?:duration|duree|dauer|length|period\w*)\b", text[max(0, a - 40): a]) else 0) + (
                2 if _DUR_STRONG.search(ctx) else 0
            )
            cands.append((score, -a, Duration(lo, hi, lab)))
    if not cands:
        return None
    cands.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return cands[0][2]


# ---------------------------------------------------------------------------------------------
# ASAP
# ---------------------------------------------------------------------------------------------

_ASAP = re.compile(
    r"\b(?:asap|as soon as possible|des que possible|des maintenant|demarrage immediat|disponible immediatement|"
    r"immediate start|immediate availability|start (?:date )?(?:is )?(?:flexible|immediate)|"
    r"flexible start|ab sofort|per sofort|so schnell wie moglich)\b"
)
_ASAP_WEAK = re.compile(r"\b(?:immediately|immediat|immediate)\b")


# ---------------------------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------------------------

def extract(
    title: str = "",
    description: str = "",
    start_hint: str = "",
    ref: date | None = None,
) -> Extraction:
    ref = ref or date.today()
    out = Extraction()
    implied: Duration | None = None

    for text_raw, default_role in ((start_hint, "start"), (title, "start"), (description, None)):
        text = norm_lines(text_raw)
        if not text:
            continue
        mentions = _scan(text, ref)
        _assign_roles(text, mentions, default_role)
        for m in mentions:
            if m.kind == "year" and m.label.isdigit() and m.role != "start":
                continue
            if m.role == "start":
                out.starts.append(m)
            elif m.role == "end":
                out.ends.append(m)
        # durée implicite : une plage début→fin
        for a, b in zip(mentions, mentions[1:]):
            if a.role == "start" and b.role == "end" and a.start and b.end and implied is None:
                days = (b.end - a.start).days + 1
                months = round(days / 30.44, 1)
                if 0.5 <= months <= 36:
                    implied = Duration(months, months, f"{months:g} mois", implied=True)
        if _ASAP.search(text) or (_ASAP_WEAK.search(text) and _role_hint_asap(text)):
            out.asap = True

    # une date explicite (mot-clé) l'emporte sur une date « faible » déduite du titre
    if any(not m.weak and not m.year_inferred for m in out.starts):
        out.starts = [m for m in out.starts if not m.weak]
    # dédoublonnage des débuts
    seen, uniq = set(), []
    for m in out.starts:
        k = (m.start, m.end)
        if k not in seen:
            seen.add(k)
            uniq.append(m)
    out.starts = uniq

    full = norm_lines(" \n".join([title, description]))
    out.duration = extract_duration(full) or implied
    return out


def _role_hint_asap(text: str) -> bool:
    for m in _ASAP_WEAK.finditer(text):
        pre = text[max(0, m.start() - 40): m.start()]
        if re.search(r"\b(?:start\w*|begin\w*|join\w*|debut\w*|demarr\w*|available|disponible|integr\w*)\b", pre):
            return True
    return False


# ---------------------------------------------------------------------------------------------
# Comparaison à la fenêtre de stage
# ---------------------------------------------------------------------------------------------

@dataclass
class DateVerdict:
    status: str                         # PASS | FAIL | UNKNOWN | ASAP
    start_status: str
    duration_status: str
    reasons: list[str] = field(default_factory=list)
    start_labels: list[str] = field(default_factory=list)
    duration_label: str = ""
    flags: list[str] = field(default_factory=list)


def evaluate_dates(ex: Extraction, window: dict) -> DateVerdict:
    earliest: date = window["earliest_start"]
    latest: date = window["latest_start"]
    min_m, max_m = float(window.get("min_months", 4)), float(window.get("max_months", 6))
    tol = float(window.get("duration_tolerance", 0.3))
    win_txt = f"{earliest.strftime('%d/%m/%Y')} → {latest.strftime('%d/%m/%Y')}"

    reasons: list[str] = []
    flags: list[str] = []

    # --- début ---
    start_status = "UNKNOWN"
    labels = [m.label for m in ex.starts]
    if ex.starts:
        verdicts = []
        for m in ex.starts:
            span_days = (m.end - m.start).days if m.start and m.end else 0
            if span_days > 200:
                verdicts.append(("BROAD", m))
            elif m.end < earliest:
                verdicts.append(("EARLY", m))
            elif m.start > latest:
                verdicts.append(("LATE", m))
            else:
                verdicts.append(("OK", m))
        oks = [m for v, m in verdicts if v == "OK"]
        if oks:
            start_status = "PASS"
            m = oks[0]
            reasons.append(f"début {m.label} ✓")
            if m.start < earliest or m.end > latest:
                flags.append("partial")
            if any(x.year_inferred for x in oks):
                flags.append("year_inferred")
        elif all(v == "BROAD" for v, _ in verdicts):
            start_status = "UNKNOWN"
            reasons.append(f"début trop vague ({verdicts[0][1].label})")
        else:
            start_status = "FAIL"
            v, m = next(((v, m) for v, m in verdicts if v in ("EARLY", "LATE")), verdicts[0])
            reasons.append(f"début {m.label} hors fenêtre ({'trop tôt' if v == 'EARLY' else 'trop tard'}; fenêtre {win_txt})")
    elif ex.asap:
        start_status = "ASAP"
        reasons.append("début « dès que possible » (incompatible avec une fin de cours en avril)")
    else:
        reasons.append("date de début non indiquée")

    # --- durée ---
    dur_status = "UNKNOWN"
    dur_label = ""
    if ex.duration:
        d = ex.duration
        dur_label = d.label + (" (déduite)" if d.implied else "")
        if d.max_months + tol >= min_m and d.min_months - tol <= max_m:
            dur_status = "PASS"
            reasons.append(f"durée {dur_label} ✓")
        else:
            dur_status = "FAIL"
            reasons.append(f"durée {dur_label} hors cible ({min_m:g}-{max_m:g} mois)")
    else:
        reasons.append("durée non indiquée")

    if start_status == "FAIL" or dur_status == "FAIL":
        status = "FAIL"
    elif start_status == "ASAP":
        status = "ASAP"
    elif start_status == "PASS":
        status = "PASS"
    else:
        status = "UNKNOWN"

    return DateVerdict(status, start_status, dur_status, reasons, labels, dur_label, flags)
