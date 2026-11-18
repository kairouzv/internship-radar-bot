"""Alertes e-mail des job boards (LinkedIn, Indeed, Welcome to the Jungle, eFinancialCareers, jobs.ch…).

Principe : tu crées des alertes « stage finance Paris / Genève » sur chaque site, tu les rediriges vers un
libellé Gmail (ex. « JobAlerts »), et le bot lit ce libellé en IMAP (lecture seule). Aucun scraping, aucune
infraction aux CGU : ce sont les sites eux-mêmes qui t'envoient les offres.
"""
from __future__ import annotations

import asyncio
import email
import imaplib
import re
from datetime import date, timedelta
from email import policy
from email.message import EmailMessage
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from ..http import Http
from ..models import Job
from .base import Source, register
from .feeds import _enrich_from_page

# (nom, regex d'URL, générateur d'identifiant, URL canonique)
PROVIDERS = [
    ("LinkedIn", re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?]*-)?(\d{6,})"), lambda m, u: f"li-{m[1]}",
     lambda m, u: f"https://www.linkedin.com/jobs/view/{m[1]}"),
    ("Indeed", re.compile(r"indeed\.[a-z.]+/.*[?&]jk=([0-9a-f]{8,})"), lambda m, u: f"in-{m[1]}",
     lambda m, u: f"https://{urlparse(u).netloc}/viewjob?jk={m[1]}"),
    ("Welcome to the Jungle", re.compile(r"welcometothejungle\.com/(?:fr|en)/companies/([^/]+)/jobs/([^/?#]+)"),
     lambda m, u: f"wttj-{m[1]}-{m[2]}", lambda m, u: f"https://www.welcometothejungle.com/fr/companies/{m[1]}/jobs/{m[2]}"),
    ("eFinancialCareers", re.compile(r"efinancialcareers\.[a-z.]+/.*?\.id(\d+)"), lambda m, u: f"efc-{m[1]}",
     lambda m, u: u.split("?")[0]),
    ("jobs.ch", re.compile(r"jobs\.ch/(?:en|fr|de)/vacancies/detail/([0-9a-f-]{20,})"), lambda m, u: f"jobsch-{m[1]}",
     lambda m, u: f"https://www.jobs.ch/en/vacancies/detail/{m[1]}/"),
    ("jobup.ch", re.compile(r"jobup\.ch/(?:en|fr|de)/jobs/detail/([0-9a-f-]{20,})"), lambda m, u: f"jobup-{m[1]}",
     lambda m, u: f"https://www.jobup.ch/fr/emplois/detail/{m[1]}/"),
    ("Glassdoor", re.compile(r"glassdoor\.[a-z.]+/.*jobListingId=(\d+)"), lambda m, u: f"gd-{m[1]}",
     lambda m, u: u.split("&")[0]),
    ("APEC", re.compile(r"apec\.fr/.*detail-offre/(\w+)"), lambda m, u: f"apec-{m[1]}", lambda m, u: u.split("?")[0]),
    ("HelloWork", re.compile(r"hellowork\.com/fr-fr/emplois/(\d+)\.html"), lambda m, u: f"hw-{m[1]}",
     lambda m, u: u.split("?")[0]),
    ("JobTeaser", re.compile(r"jobteaser\.com/(?:fr|en)/job-offers/([0-9a-f-]{20,})"), lambda m, u: f"jt-{m[1]}",
     lambda m, u: u.split("?")[0]),
]

GENERIC_ANCHOR = re.compile(
    r"^(?:view(?: job| details| all)?|voir(?: l'offre| l'annonce| les offres| plus| toutes)?|apply(?: now)?|postuler|"
    r"see (?:job|more|all)|more|plus|unsubscribe|se desabonner|se désabonner|manage|gerer|gérer|click here|ici|"
    r"consulter(?: l'offre)?|afficher|detail|détail|details|open|ouvrir|candidater|easy apply|"
    r"voir l’offre|offre|job)\W*$", re.I)

# Domaines dont la fiche expose du JSON-LD JobPosting (dates/durée exploitables), non « scrapés » : lecture d'une page
# publique unique par nouvelle offre. LinkedIn / Indeed / Glassdoor sont volontairement exclus.
DETAIL_DOMAINS = ("welcometothejungle.com", "jobs.ch", "jobup.ch", "efinancialcareers.", "apec.fr", "hellowork.com")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("‌", "").replace("​", "").replace("­", "")).strip()


def _container_lines(a) -> list[str]:
    """Texte du bloc qui entoure le lien (carte d'offre), en lignes non vides."""
    node = a
    for _ in range(4):
        parent = node.parent
        if parent is None or parent.name in ("body", "html"):
            break
        node = parent
        if node.name in ("td", "li", "tr", "div", "table") and len(node.find_all("a", href=True)) <= 3:
            break
    lines = [_clean(x) for x in node.get_text("\n").split("\n")]
    out = []
    for l in lines:
        if l and l not in out and not GENERIC_ANCHOR.match(l):
            out.append(l)
    return out


_CITY = r"(?:paris|gen[eè]ve|geneva|genf|la d[eé]fense|neuilly\S*|ile-de-france|île-de-france|puteaux|courbevoie)"
LOCATIONISH = re.compile(rf",|\(|^\s*{_CITY}\b|\b(?:france|suisse|switzerland|schweiz)\s*$", re.I)


def parse_alert_email(raw: bytes) -> list[dict]:
    """Retourne des dicts {provider, id, url, title, company, location} pour chaque offre trouvée dans un e-mail."""
    msg: EmailMessage = email.message_from_bytes(raw, policy=policy.default)  # type: ignore[assignment]
    body = msg.get_body(preferencelist=("html", "plain"))
    if body is None:
        return []
    content = body.get_content()
    if body.get_content_type() != "text/html":
        content = "<pre>" + content.replace("<", "&lt;") + "</pre>"
    soup = BeautifulSoup(content, "html.parser")
    jobs: dict[str, dict] = {}
    for a in soup.find_all("a", href=True):
        url = a["href"].strip()
        for provider, rx, mk_id, mk_url in PROVIDERS:
            m = rx.search(url)
            if not m:
                continue
            jid = mk_id(m, url)
            text = _clean(a.get_text(" "))
            lines = _container_lines(a)
            entry = jobs.get(jid)
            if entry is None:
                entry = jobs[jid] = {"provider": provider, "id": jid, "url": mk_url(m, url), "title": "", "company": "",
                                     "location": "", "lines": lines}
            if text and not GENERIC_ANCHOR.match(text) and len(text) > len(entry["title"]) and len(text) < 160:
                entry["title"] = text
            break
    out = []
    for e in jobs.values():
        lines = e.pop("lines")
        if not e["title"] and lines:
            e["title"] = lines[0]
        if not e["title"]:
            continue
        rest: list[str] = []
        for l in lines:
            if l == e["title"] or len(l) >= 120:
                continue
            # LinkedIn : « Entreprise · Lieu » sur une seule ligne
            rest.extend(p.strip() for p in re.split(r"\s[·|•]\s", l) if p.strip())
        loc = next((l for l in rest if LOCATIONISH.search(l)), "")
        comp = next((l for l in rest if l != loc and not LOCATIONISH.search(l)), "")
        e["company"], e["location"] = comp, loc
        out.append(e)
    return out


@register
class EmailAlerts(Source):
    """cfg: folder (libellé Gmail, défaut 'INBOX'), days (fenêtre, défaut 3), host (défaut imap.gmail.com),
    senders (sous-chaînes d'expéditeur à lire). Secrets : IMAP_USER, IMAP_PASSWORD (mot de passe d'application)."""
    type = "email"
    uses_store = True
    DEFAULT_SENDERS = ["linkedin", "indeed", "welcometothejungle", "efinancialcareers", "jobs.ch", "jobup",
                       "glassdoor", "apec", "hellowork", "jobteaser", "adzuna"]

    def __init__(self, cfg, secret=None):
        super().__init__(cfg, secret)
        self.store = None

    def bind(self, store) -> None:
        self.store = store

    def missing(self) -> str | None:
        if not (self.secret("IMAP_USER") and self.secret("IMAP_PASSWORD")):
            return "secrets IMAP_USER / IMAP_PASSWORD absents"
        return None

    # ── IMAP (bloquant, exécuté dans un thread) ─────────────────────────────────────────────
    def _fetch_sync(self) -> tuple[list[dict], list[str]]:
        host = self.secret("IMAP_HOST") or self.cfg.get("host", "imap.gmail.com")
        folder = self.cfg.get("folder", "INBOX")
        since = (date.today() - timedelta(days=int(self.cfg.get("days", 3)))).strftime("%d-%b-%Y")
        senders = [s.lower() for s in (self.cfg.get("senders") or self.DEFAULT_SENDERS)]
        parsed: list[dict] = []
        msg_ids: list[str] = []
        M = imaplib.IMAP4_SSL(host, timeout=30)
        try:
            M.login(self.secret("IMAP_USER"), self.secret("IMAP_PASSWORD"))
            typ, _ = M.select(f'"{folder}"', readonly=True)
            if typ != "OK":
                raise RuntimeError(f"dossier/libellé IMAP introuvable : {folder}")
            typ, data = M.search(None, "SINCE", since)
            nums = data[0].split() if typ == "OK" and data and data[0] else []
            for num in nums[-300:]:
                typ, hdr = M.fetch(num, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM)])")
                if typ != "OK" or not hdr or not hdr[0]:
                    continue
                h = email.message_from_bytes(hdr[0][1], policy=policy.default)
                mid = (h.get("Message-ID") or "").strip() or f"{folder}-{num.decode()}"
                frm = str(h.get("From") or "").lower()
                if self.store is not None and self.store.mail_seen(mid):
                    continue
                if not any(s in frm for s in senders):
                    msg_ids.append(mid)       # mail non pertinent : mémorisé pour ne plus le relire
                    continue
                typ, full = M.fetch(num, "(BODY.PEEK[])")
                if typ != "OK" or not full or not full[0]:
                    continue
                parsed.extend(parse_alert_email(full[0][1]))
                msg_ids.append(mid)
        finally:
            try:
                M.logout()
            except Exception:  # noqa: BLE001
                pass
        return parsed, msg_ids

    async def fetch(self, http: Http) -> list[Job]:
        parsed, msg_ids = await asyncio.to_thread(self._fetch_sync)
        if self.store is not None:
            for mid in msg_ids:
                self.store.add_mail(mid)
        jobs: dict[str, Job] = {}
        for e in parsed:
            url = e["url"]
            needs = any(d in url for d in DETAIL_DOMAINS)
            jobs.setdefault(e["id"], self.job(
                e["id"], e["title"], url, company=e["company"] or e["provider"], location=e["location"],
                needs_detail=needs, extra={"provider": e["provider"]},
            ))
        return list(jobs.values())

    async def enrich(self, http: Http, job: Job) -> Job:
        return await _enrich_from_page(http, job)
