"""Source « auto » : tu donnes l'URL de la page carrières, le bot détecte l'ATS (Workday, Greenhouse, Lever…)
et mémorise la configuration trouvée. Si l'ATS change ou casse, il re-détecte tout seul."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from ..http import Http
from ..models import Job
from .base import REGISTRY, Source, register
from .discover import discover

PRIORITY = ["workday", "oracle_hcm", "greenhouse", "lever", "smartrecruiters", "ashby", "workable", "recruitee", "personio", "rss"]
KEEP_KEYS = ("search", "company", "trust_internship", "location_hint", "max_results", "country", "facets")
CACHE_DAYS = 14


class ResolveError(Exception):
    pass


@register
class Auto(Source):
    """cfg: url (page carrières). Les autres clés (search, location_hint…) sont transmises à la source détectée."""
    type = "auto"
    uses_store = True

    def __init__(self, cfg, secret=None):
        super().__init__(cfg, secret)
        self.store = None
        self._delegate: Source | None = None

    def bind(self, store) -> None:
        self.store = store

    def _cache_key(self) -> str:
        return f"auto:{self.name}"

    def _cached(self) -> dict | None:
        if self.store is None:
            return None
        raw = self.store.get_meta(self._cache_key())
        if not raw:
            return None
        try:
            data = json.loads(raw)
            at = datetime.fromisoformat(data["at"])
            if datetime.now(timezone.utc) - at < timedelta(days=CACHE_DAYS):
                return data["cfg"]
        except (ValueError, KeyError):
            pass
        return None

    async def _resolve(self, http: Http) -> dict:
        res = await discover(http, self.cfg["url"], self.name)
        sugg = sorted(res["suggestions"], key=lambda c: PRIORITY.index(c["type"]) if c["type"] in PRIORITY else 99)
        if not sugg:
            note = res["notes"][0] if res["notes"] else "aucun ATS reconnu"
            raise ResolveError(f"{self.cfg['url']} (HTTP {res['status']}) : {note}")
        cfg = dict(sugg[0])
        for k in KEEP_KEYS:
            if k in self.cfg:
                cfg[k] = self.cfg[k]
        if self.store is not None:
            self.store.set_meta(self._cache_key(), json.dumps({"cfg": cfg, "at": datetime.now(timezone.utc).isoformat()}))
        return cfg

    def _build(self, cfg: dict) -> Source:
        return REGISTRY[cfg["type"]](cfg, self.secret)

    async def fetch(self, http: Http) -> list[Job]:
        cached = self._cached()
        cfg = cached or await self._resolve(http)
        delegate = self._build(cfg)
        try:
            jobs = await delegate.fetch(http)
        except Exception:
            if not cached:
                raise
            # la config mémorisée ne marche plus : on re-détecte une fois
            cfg = await self._resolve(http)
            delegate = self._build(cfg)
            jobs = await delegate.fetch(http)
        self._delegate = delegate
        for j in jobs:
            j.source = self.key
            j.extra["via"] = delegate.key
        return jobs

    async def enrich(self, http: Http, job: Job) -> Job:
        if self._delegate is None:
            return job
        job.source = self._delegate.key
        try:
            return await self._delegate.enrich(http, job)
        finally:
            job.source = self.key
