"""Client HTTP asynchrone partagé : concurrence bornée, retries, politesse."""
from __future__ import annotations

import asyncio
import random
from collections import defaultdict
from typing import Any
from urllib.parse import urlparse

import httpx

USER_AGENT = (
    "Mozilla/5.0 (compatible; InternshipRadar/1.0; +personal job-alert bot; low-volume polling)"
)


class HttpError(Exception):
    pass


class Http:
    """Enveloppe httpx.AsyncClient : limite globale + limite par hôte + retries sur 429/5xx."""

    def __init__(self, client: httpx.AsyncClient | None = None, *, global_limit: int = 16, host_limit: int = 4,
                 timeout: float = 25.0, retries: int = 2):
        self.client = client or httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"},
        )
        self._owns = client is None
        self._global = asyncio.Semaphore(global_limit)
        self._host_limit = host_limit
        self._hosts: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(self._host_limit))
        self.retries = retries
        self.request_count = 0

    async def aclose(self) -> None:
        if self._owns:
            await self.client.aclose()

    async def request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        host = urlparse(url).netloc
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                async with self._global, self._hosts[host]:
                    self.request_count += 1
                    r = await self.client.request(method, url, **kw)
                if r.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    wait = float(r.headers.get("retry-after", 0) or 0) or (1.5 * (attempt + 1))
                    await asyncio.sleep(min(wait, 20) + random.random())
                    continue
                return r
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last = e
                if attempt < self.retries:
                    await asyncio.sleep(1.0 * (attempt + 1) + random.random())
                    continue
        raise HttpError(f"{method} {url} : {type(last).__name__ if last else 'échec'} {last or ''}".strip())

    async def get_json(self, url: str, **kw: Any) -> Any:
        r = await self.request("GET", url, **kw)
        if r.status_code >= 400:
            raise HttpError(f"GET {url} -> HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError as e:
            raise HttpError(f"GET {url} : réponse non JSON") from e

    async def post_json(self, url: str, json: Any = None, **kw: Any) -> Any:
        r = await self.request("POST", url, json=json, **kw)
        if r.status_code >= 400:
            raise HttpError(f"POST {url} -> HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError as e:
            raise HttpError(f"POST {url} : réponse non JSON") from e

    async def get_text(self, url: str, **kw: Any) -> str:
        r = await self.request("GET", url, **kw)
        if r.status_code >= 400:
            raise HttpError(f"GET {url} -> HTTP {r.status_code}")
        return r.text
