"""APIs officielles gratuites d'agrégateurs : France Travail (ex-Pôle emploi) et Adzuna."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..http import Http, HttpError
from ..models import Job
from ..textutil import html_to_text
from .base import Source, register


@register
class FranceTravail(Source):
    """API « Offres d'emploi v2 » (gratuite). Secrets : FRANCETRAVAIL_CLIENT_ID / FRANCETRAVAIL_CLIENT_SECRET
    (inscription sur francetravail.io, application + abonnement à l'API Offres d'emploi).
    cfg: search (liste de mots-clés), departements (['75','92']), days (fenêtre de récence, défaut 5)."""
    type = "francetravail"
    TOKEN_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token"
    SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"

    def missing(self) -> str | None:
        if not (self.secret("FRANCETRAVAIL_CLIENT_ID") and self.secret("FRANCETRAVAIL_CLIENT_SECRET")):
            return "secrets FRANCETRAVAIL_CLIENT_ID / FRANCETRAVAIL_CLIENT_SECRET absents"
        return None

    async def _token(self, http: Http) -> str:
        r = await http.request(
            "POST", self.TOKEN_URL, params={"realm": "/partenaire"},
            data={"grant_type": "client_credentials", "client_id": self.secret("FRANCETRAVAIL_CLIENT_ID"),
                  "client_secret": self.secret("FRANCETRAVAIL_CLIENT_SECRET"), "scope": "api_offresdemploiv2 o2dsoffre"},
        )
        if r.status_code != 200:
            raise HttpError(f"France Travail OAuth -> HTTP {r.status_code}")
        return r.json()["access_token"]

    async def fetch(self, http: Http) -> list[Job]:
        token = await self._token(http)
        days = int(self.cfg.get("days", 5))
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        until = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        found: dict[str, Job] = {}
        for dep in self.cfg.get("departements", ["75", "92"]):
            for term in self.cfg.get("search") or ["stage finance", "stage m&a", "stage private equity"]:
                r = await http.request(
                    "GET", self.SEARCH_URL, headers={"Authorization": f"Bearer {token}"},
                    params={"motsCles": term, "departement": dep, "range": "0-149", "sort": 1,
                            "minCreationDate": since, "maxCreationDate": until},
                )
                if r.status_code == 204:
                    continue
                if r.status_code not in (200, 206):
                    raise HttpError(f"France Travail search -> HTTP {r.status_code}")
                for o in r.json().get("resultats", []):
                    url = (o.get("origineOffre") or {}).get("urlOrigine") or \
                        f"https://candidat.francetravail.fr/offres/recherche/detail/{o['id']}"
                    found.setdefault(o["id"], self.job(
                        o["id"], o.get("intitule"), url,
                        company=(o.get("entreprise") or {}).get("nom") or "Entreprise non communiquée",
                        location=(o.get("lieuTravail") or {}).get("libelle", ""),
                        description=o.get("description") or "",
                        contract=" ".join(filter(None, [o.get("typeContratLibelle"), o.get("natureContrat")])),
                    ))
        return list(found.values())


@register
class Adzuna(Source):
    """API Adzuna (clé gratuite sur developer.adzuna.com). Secrets : ADZUNA_APP_ID / ADZUNA_APP_KEY.
    cfg: country ('fr' ou 'ch'), where ('Paris' / 'Genève'), search (liste de requêtes), days (défaut 5)."""
    type = "adzuna"

    def missing(self) -> str | None:
        if not (self.secret("ADZUNA_APP_ID") and self.secret("ADZUNA_APP_KEY")):
            return "secrets ADZUNA_APP_ID / ADZUNA_APP_KEY absents"
        return None

    async def fetch(self, http: Http) -> list[Job]:
        country = self.cfg.get("country", "fr")
        found: dict[str, Job] = {}
        for term in self.cfg.get("search") or ["stage finance", "stage m&a", "internship investment banking"]:
            data = await http.get_json(
                f"https://api.adzuna.com/v1/api/jobs/{country}/search/1",
                params={"app_id": self.secret("ADZUNA_APP_ID"), "app_key": self.secret("ADZUNA_APP_KEY"),
                        "what": term, "where": self.cfg.get("where", ""), "results_per_page": 50,
                        "sort_by": "date", "max_days_old": int(self.cfg.get("days", 5)), "content-type": "application/json"},
            )
            for o in data.get("results", []):
                found.setdefault(str(o["id"]), self.job(
                    o["id"], html_to_text(o.get("title")), o.get("redirect_url", ""),
                    company=(o.get("company") or {}).get("display_name", "") or "Entreprise non communiquée",
                    location=(o.get("location") or {}).get("display_name", ""),
                    description=html_to_text(o.get("description")),
                    contract=" ".join(filter(None, [o.get("contract_type"), o.get("contract_time")])),
                ))
        return list(found.values())
