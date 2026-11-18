"""Modèles de données partagés."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .textutil import norm, norm_company, norm_title


@dataclass
class Job:
    source: str                 # ex. "workday:Lazard"
    source_id: str              # identifiant stable côté source
    title: str
    company: str
    url: str
    location: str = ""
    description: str = ""
    contract: str = ""          # type de contrat brut fourni par la source (Intern, Full-time...)
    department: str = ""
    start_hint: str = ""        # date de début brute fournie par la source, si elle existe
    posted_at: datetime | None = None
    needs_detail: bool = False  # la description complète n'est pas encore chargée
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def uid(self) -> str:
        return f"{self.source}|{self.source_id}"

    @property
    def fingerprint(self) -> str:
        """Empreinte inter-sources : même entreprise + même titre + même ville = même offre."""
        city = norm(self.location).split(",")[0].split(" - ")[0].strip()
        city = "".join(ch for ch in city if ch.isalnum())
        raw = f"{norm_company(self.company)}|{norm_title(self.title)}|{city}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]
