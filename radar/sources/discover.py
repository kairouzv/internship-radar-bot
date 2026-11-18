"""Détection automatique de l'ATS d'une page carrières : donne la configuration prête à coller dans sources.yaml."""
from __future__ import annotations

import re

import yaml

from ..http import Http
from .feeds import parse_jobposting_jsonld

_RESERVED_WORKDAY_SITES = {"wday", "cxs", "job", "details", "login", "assets", "en-us", "fr-fr"}

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("workday", re.compile(r"https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Za-z]{2}/)?([A-Za-z0-9_.%-]+)")),
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9_-]+)")),
    ("lever", re.compile(r"jobs(\.eu)?\.lever\.co/([a-z0-9_-]+)")),
    ("smartrecruiters", re.compile(r"(?:(?:jobs|careers)\.smartrecruiters\.com|api\.smartrecruiters\.com/v1/companies)/([A-Za-z0-9_-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)")),
    ("workable", re.compile(r"apply\.workable\.com/([a-z0-9_-]+)")),
    ("recruitee", re.compile(r"([a-z0-9-]+)\.recruitee\.com")),
    ("personio", re.compile(r"([a-z0-9-]+)\.jobs\.personio\.(de|com)")),
    ("teamtailor", re.compile(r"([a-z0-9-]+)\.teamtailor\.com")),
    ("oracle_hcm", re.compile(r"([a-z0-9.-]+\.fa\.[a-z0-9.-]*oraclecloud\.com)/hcmUI/CandidateExperience/(?:[a-z]{2}/)?sites/([A-Za-z0-9_]+)")),
]

_UNSUPPORTED = {
    "Taleo (Oracle)": r"taleo\.net|\.tal\.net",
    "SuccessFactors (SAP)": r"successfactors|sapsf\.|jobs2web",
    "iCIMS": r"icims\.com",
    "Phenom": r"phenompeople|phenom\.com|cdn\.phenompeople",
    "Eightfold": r"eightfold\.ai",
    "BrassRing (IBM)": r"brassring\.com",
    "Jobvite": r"jobvite\.com",
}


def detect(url: str, page_html: str, name: str) -> dict:
    """Retourne {'suggestions': [cfg...], 'notes': [str...]} pour une page donnée."""
    blob = page_html.replace("\\/", "/") + "\n" + url
    sugg: list[dict] = []
    seen: set[tuple] = set()

    def add(cfg: dict) -> None:
        key = tuple(sorted(cfg.items()))
        if key not in seen:
            seen.add(key)
            sugg.append(cfg)

    for typ, rx in _PATTERNS:
        for m in rx.finditer(blob):
            g = m.groups()
            if typ == "workday":
                tenant, wd, site = g
                site = site.split("?")[0]
                if site.lower() in _RESERVED_WORKDAY_SITES:
                    continue
                add({"name": name, "type": "workday", "host": f"{tenant}.{wd}.myworkdayjobs.com", "site": site})
            elif typ == "greenhouse":
                if g[0] in ("embed", "v1"):
                    continue
                add({"name": name, "type": "greenhouse", "board": g[0]})
            elif typ == "lever":
                cfg = {"name": name, "type": "lever", "site": g[1]}
                if g[0]:
                    cfg["region"] = "eu"
                add(cfg)
            elif typ == "smartrecruiters":
                add({"name": name, "type": "smartrecruiters", "company": g[0]})
            elif typ == "ashby":
                add({"name": name, "type": "ashby", "board": g[0]})
            elif typ == "workable":
                if g[0] not in ("api", "www"):
                    add({"name": name, "type": "workable", "account": g[0]})
            elif typ == "recruitee":
                if g[0] not in ("www", "api", "careers"):
                    add({"name": name, "type": "recruitee", "company": g[0]})
            elif typ == "personio":
                add({"name": name, "type": "personio", "company": g[0], "tld": g[1]})
            elif typ == "teamtailor":
                if g[0] not in ("www", "app", "career"):
                    add({"name": name, "type": "rss", "url": f"https://{g[0]}.teamtailor.com/jobs.rss"})
            elif typ == "oracle_hcm":
                add({"name": name, "type": "oracle_hcm", "host": g[0], "site": g[1]})

    notes: list[str] = []
    if not sugg:
        for label, rx in _UNSUPPORTED.items():
            if re.search(rx, blob, re.I):
                notes.append(f"ATS détecté : {label} (pas d'API publique supportée) → utilise un flux RSS/sitemap, "
                             f"une source 'html', ou les alertes e-mail des job boards.")
        if parse_jobposting_jsonld(page_html):
            notes.append("La page contient des offres JSON-LD (schema.org/JobPosting) → source 'html' possible.")
        if not notes:
            notes.append("Aucun ATS reconnu dans le HTML statique (page probablement rendue en JavaScript). "
                         "Ouvre l'onglet Réseau du navigateur : cherche une URL contenant 'myworkdayjobs', 'greenhouse', "
                         "'lever.co', 'smartrecruiters', 'oraclecloud' puis relance 'radar discover' avec cette URL.")
    return {"suggestions": sugg, "notes": notes}


async def discover(http: Http, url: str, name: str) -> dict:
    r = await http.request("GET", url)
    result = detect(str(r.url), r.text if r.status_code < 400 else "", name)
    result["status"] = r.status_code
    result["final_url"] = str(r.url)
    return result


def to_yaml(suggestions: list[dict]) -> str:
    return yaml.safe_dump(suggestions, allow_unicode=True, sort_keys=False, default_flow_style=None)
