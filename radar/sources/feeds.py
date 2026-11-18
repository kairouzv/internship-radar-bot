"""Collecteurs génériques : flux RSS/Atom, pages HTML (liens + JSON-LD), sitemaps.
Utiles pour les pages carrières sans API publique (ATS non supporté ou site maison)."""
from __future__ import annotations

import json
import re
from datetime import datetime
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup
from defusedxml import ElementTree as ET

from ..http import Http, HttpError
from ..models import Job
from ..textutil import html_to_text
from .base import Source, register


# ── JSON-LD (schema.org/JobPosting) ───────────────────────────────────────────────────────────
def parse_jobposting_jsonld(page_html: str) -> list[dict]:
    soup = BeautifulSoup(page_html, "html.parser")
    out: list[dict] = []

    def walk(node):
        if isinstance(node, list):
            for n in node:
                walk(n)
        elif isinstance(node, dict):
            t = node.get("@type")
            types = t if isinstance(t, list) else [t]
            if "JobPosting" in types:
                out.append(node)
            for k in ("@graph", "mainEntity", "itemListElement", "item"):
                if k in node:
                    walk(node[k])

    for s in soup.find_all("script", type="application/ld+json"):
        raw = (s.string or s.get_text() or "").strip()
        if not raw:
            continue
        try:
            walk(json.loads(raw))
        except ValueError:
            try:
                walk(json.loads(re.sub(r"[\x00-\x1f]+", " ", raw)))
            except ValueError:
                continue
    return out


def _jsonld_location(node: dict) -> str:
    loc = node.get("jobLocation")
    locs = loc if isinstance(loc, list) else [loc] if loc else []
    names = []
    for l in locs:
        if not isinstance(l, dict):
            continue
        a = l.get("address") or {}
        if isinstance(a, str):
            names.append(a)
            continue
        parts = [a.get("addressLocality"), a.get("addressRegion"), a.get("addressCountry") if isinstance(a.get("addressCountry"), str) else (a.get("addressCountry") or {}).get("name")]
        names.append(", ".join(p for p in parts if p))
    return "; ".join(n for n in names if n)


def _jsonld_to_fields(node: dict) -> dict:
    org = node.get("hiringOrganization")
    emp = node.get("employmentType")
    ident = node.get("identifier")
    return dict(
        title=node.get("title") or "",
        description=html_to_text(node.get("description") or ""),
        location=_jsonld_location(node),
        contract=", ".join(emp) if isinstance(emp, list) else (emp or ""),
        company=(org.get("name") if isinstance(org, dict) else org) or "",
        ident=(ident.get("value") if isinstance(ident, dict) else ident) or "",
        url=node.get("url") or "",
        posted=node.get("datePosted") or "",
    )


async def _enrich_from_page(http: Http, job: Job) -> Job:
    page = await http.get_text(job.url)
    postings = parse_jobposting_jsonld(page)
    if postings:
        f = _jsonld_to_fields(postings[0])
        job.description = f["description"] or job.description
        job.location = f["location"] or job.location
        job.contract = f["contract"] or job.contract
        if f["title"] and (not job.title or len(job.title) < 4):
            job.title = f["title"]
    else:
        soup = BeautifulSoup(page, "html.parser")
        h1 = soup.find("h1")
        if h1 and (not job.title or job.extra.get("slug_title")):
            job.title = h1.get_text(" ", strip=True)
        main = soup.find("main") or soup.find("article") or soup.body or soup
        job.description = html_to_text(str(main))[:6000]
        if not job.location:
            # lieu probable dans les premiers blocs : on laisse le classifieur chercher dans le titre
            pass
    job.needs_detail = False
    return job


# ── RSS / Atom ────────────────────────────────────────────────────────────────────────────────
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


@register
class RSS(Source):
    """cfg: url (flux RSS/Atom). Compatible Teamtailor (…/jobs.rss), Welcome-to-the-Jungle-like feeds, etc."""
    type = "rss"

    async def fetch(self, http: Http) -> list[Job]:
        xml = await http.get_text(self.cfg["url"])
        root = ET.fromstring(xml)
        out = []
        for item in root.iter():
            if _local(item.tag) not in ("item", "entry"):
                continue
            fields: dict[str, str] = {}
            loc_parts: list[str] = []
            for ch in item:
                name = _local(ch.tag)
                if name == "link":
                    fields.setdefault("link", ch.get("href") or (ch.text or ""))
                elif name in ("city", "location", "country"):
                    if (ch.text or "").strip():
                        loc_parts.append(ch.text.strip())
                    for sub in ch:
                        if (sub.text or "").strip():
                            loc_parts.append(sub.text.strip())
                elif name in ("locations",):
                    for sub in ch.iter():
                        if _local(sub.tag) in ("city", "country", "name") and (sub.text or "").strip():
                            loc_parts.append(sub.text.strip())
                else:
                    fields.setdefault(name, (ch.text or "").strip())
            link = fields.get("link", "")
            sid = fields.get("guid") or fields.get("id") or link
            if not sid:
                continue
            out.append(self.job(
                sid, fields.get("title", ""), link,
                location=", ".join(dict.fromkeys(loc_parts)),
                description=html_to_text(fields.get("description") or fields.get("summary") or fields.get("content") or ""),
            ))
        return out


# ── Page HTML ─────────────────────────────────────────────────────────────────────────────────
@register
class HtmlPage(Source):
    """cfg: url (ou urls), link_selector (sélecteur CSS des liens d'offres), include_regex/exclude_regex (sur l'URL),
    detail (true par défaut : ouvre chaque nouvelle offre pour lire description / JSON-LD)."""
    type = "html"

    def _urls(self) -> list[str]:
        u = self.cfg.get("urls") or [self.cfg["url"]]
        return [u] if isinstance(u, str) else list(u)

    async def fetch(self, http: Http) -> list[Job]:
        inc = re.compile(self.cfg["include_regex"]) if self.cfg.get("include_regex") else None
        exc = re.compile(self.cfg["exclude_regex"]) if self.cfg.get("exclude_regex") else None
        sel = self.cfg.get("link_selector", "a[href]")
        found: dict[str, Job] = {}
        for page_url in self._urls():
            page = await http.get_text(page_url)
            soup = BeautifulSoup(page, "html.parser")
            for node in parse_jobposting_jsonld(page):
                f = _jsonld_to_fields(node)
                url = f["url"] or page_url
                sid = f["ident"] or url
                found.setdefault(str(sid), self.job(sid, f["title"], url, location=f["location"],
                                                    description=f["description"], contract=f["contract"]))
            for a in soup.select(sel):
                href = a.get("href")
                if not href or href.startswith(("#", "javascript:", "mailto:")):
                    continue
                url = urljoin(self.cfg.get("base_url", page_url), href).split("#")[0]
                if (inc and not inc.search(url)) or (exc and exc.search(url)):
                    continue
                lines = [l.strip() for l in a.get_text("\n").split("\n") if l.strip()]
                title = lines[0] if lines else (a.get("title") or a.get("aria-label") or "")
                rest = " · ".join(lines[1:])
                if not title:
                    continue
                found.setdefault(url, self.job(
                    url, title, url,
                    location=rest if 0 < len(rest) < 80 else "",
                    description=rest if len(rest) >= 80 else "",
                    needs_detail=self.cfg.get("detail", True),
                ))
        return list(found.values())

    async def enrich(self, http: Http, job: Job) -> Job:
        return await _enrich_from_page(http, job)


# ── Sitemap ───────────────────────────────────────────────────────────────────────────────────
@register
class Sitemap(Source):
    """cfg: url (sitemap.xml ou index), url_regex (ne garder que les URLs d'offres)."""
    type = "sitemap"

    async def _urls(self, http: Http, url: str, depth: int = 0) -> list[str]:
        xml = await http.get_text(url)
        root = ET.fromstring(xml)
        locs = [(el.text or "").strip() for el in root.iter() if _local(el.tag) == "loc" and el.text]
        if _local(root.tag) == "sitemapindex" and depth < 2:
            out: list[str] = []
            pat = re.compile(self.cfg["sitemap_regex"]) if self.cfg.get("sitemap_regex") else None
            for child in [l for l in locs if not pat or pat.search(l)][:8]:
                try:
                    out.extend(await self._urls(http, child, depth + 1))
                except HttpError:
                    continue
            return out
        return locs

    @staticmethod
    def _slug_title(url: str) -> str:
        slug = unquote(urlparse(url).path.rstrip("/").rsplit("/", 1)[-1])
        slug = re.sub(r"[-_]+", " ", slug)
        slug = re.sub(r"\b[A-Za-z]?\d{4,}\b", "", slug)
        return re.sub(r"\s+", " ", slug).strip().title()

    async def fetch(self, http: Http) -> list[Job]:
        pat = re.compile(self.cfg.get("url_regex", r"."))
        urls = [u for u in await self._urls(http, self.cfg["url"]) if pat.search(u)]
        return [self.job(u, self._slug_title(u), u, needs_detail=True, extra={"slug_title": True}) for u in urls[:2000]]

    async def enrich(self, http: Http, job: Job) -> Job:
        return await _enrich_from_page(http, job)
