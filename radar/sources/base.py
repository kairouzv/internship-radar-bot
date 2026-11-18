"""Socle commun des collecteurs."""
from __future__ import annotations

from typing import Callable, ClassVar

from ..http import Http
from ..models import Job

REGISTRY: dict[str, type["Source"]] = {}


def register(cls: type["Source"]) -> type["Source"]:
    REGISTRY[cls.type] = cls
    return cls


class Source:
    """Un collecteur = une entreprise (ou un job board) à surveiller."""

    type: ClassVar[str] = ""
    uses_store: ClassVar[bool] = False      # True : la source a besoin de la base d'état (ex. courriers déjà lus)
    # Mots utilisés pour limiter côté serveur les résultats quand l'API le permet.
    DEFAULT_TERMS: ClassVar[list[str]] = ["intern", "internship", "stage", "stagiaire"]

    def __init__(self, cfg: dict, secret: Callable[[str], str] | None = None):
        self.cfg = cfg
        self.name: str = cfg["name"]
        self.company: str = cfg.get("company", self.name)
        self.secret = secret or (lambda _n: "")

    @property
    def key(self) -> str:
        return f"{self.type}:{self.name}"

    @property
    def terms(self) -> list[str]:
        return list(self.cfg.get("search") or self.DEFAULT_TERMS)

    def job(self, source_id, title, url, **kw) -> Job:
        kw.setdefault("company", self.company)
        extra = kw.setdefault("extra", {})
        if self.cfg.get("trust_internship"):
            extra["trust_internship"] = True
        if not kw.get("location") and self.cfg.get("location_hint"):
            kw["location"] = self.cfg["location_hint"]
        return Job(source=self.key, source_id=str(source_id), title=(title or "").strip(), url=url, **kw)

    async def fetch(self, http: Http) -> list[Job]:  # pragma: no cover - interface
        raise NotImplementedError

    async def enrich(self, http: Http, job: Job) -> Job:
        """Charge la description complète quand la liste n'en fournit pas. Par défaut : rien à faire."""
        return job

    def bind(self, store) -> None:  # noqa: B027 - hook optionnel
        """Reçoit la base d'état si uses_store est vrai."""

    def missing(self) -> str | None:
        """Retourne un message si la source n'est pas configurable (secret manquant...). None = OK."""
        return None
