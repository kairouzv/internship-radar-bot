"""Canal Notion : une ligne par alerte dans la base « Internship Radar ».

Le bot ne fait que CRÉER des lignes. Il ne modifie jamais une ligne existante : le Statut
(Pas encore, Candidaté…) et tout ce que tu édites à la main restent intacts.
"""
from __future__ import annotations

import re

from ..http import Http, HttpError
from .channels import Channel
from .format import Alert, Text

API = "https://api.notion.com/v1/pages"
VERSION = "2022-06-28"
MATCH = {"TOP": "Top", "GOOD": "Bon", "CHECK": "À vérifier"}


def database_id(raw: str) -> str:
    """Accepte l'identifiant seul ou l'URL complète de la base."""
    m = re.search(r"[0-9a-fA-F]{32}", (raw or "").replace("-", ""))
    return m.group(0).lower() if m else (raw or "").strip()


def _text(s: str, limit: int = 1900) -> dict:
    return {"rich_text": [{"text": {"content": (s or "")[:limit]}}]}


def _option(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace(",", " ")).strip()[:90]


def _start(alert: Alert) -> str:
    v = alert.verdict
    labels = v.dates.start_labels if v.dates else []
    status = v.dates.start_status if v.dates else "UNKNOWN"
    if status == "ASAP":
        return "dès que possible"
    if labels and status in ("PASS", "FAIL"):
        return labels[0] + (" ❌" if status == "FAIL" else "")
    return "non indiqué"


def _comment(alert: Alert) -> str:
    v = alert.verdict
    parts = list(v.reasons[:8])
    w = alert.warning()
    if w:
        parts.append("⚠️ " + w)
    return " · ".join(parts)


def properties(alert: Alert) -> dict:
    j, v = alert.job, alert.verdict
    prov = j.extra.get("provider") or j.source.split(":", 1)[-1]
    duration = v.dates.duration_label if v.dates and v.dates.duration_label else "non indiquée"
    props: dict = {
        "Poste": {"title": [{"text": {"content": (j.title or "(sans titre)")[:200]}}]},
        "Entreprise": _text(j.company, 200),
        "Match": {"select": {"name": MATCH.get(v.tier, "À vérifier")}},
        "Score": {"number": v.score},
        "Début": _text(_start(alert), 200),
        "Durée": _text(duration, 200),
        "Lieu": _text(j.location or "", 200),
        "Desk": {"multi_select": [{"name": n} for n in dict.fromkeys(_option(d) for d in v.desks) if n]},
        "Commentaire": _text(_comment(alert)),
        "Source": _text(str(prov), 200),
    }
    if j.url:
        props["Lien"] = {"url": j.url[:1900]}
    return props


class NotionDB(Channel):
    name = "notion"

    def __init__(self, token: str, db_id: str):
        self.token, self.db_id = token, database_id(db_id)

    async def send_alert(self, http: Http, alert: Alert) -> None:
        r = await http.request(
            "POST", API,
            headers={"Authorization": f"Bearer {self.token}", "Notion-Version": VERSION, "Content-Type": "application/json"},
            json={"parent": {"database_id": self.db_id}, "properties": properties(alert)},
        )
        if r.status_code != 200:
            try:
                body = r.json()
                desc = f"{body.get('code', '')} {body.get('message', '')}".strip()
            except ValueError:
                desc = r.text[:120]
            hint = " (partage la base avec l'intégration : ••• > Connexions)" if r.status_code == 404 else ""
            raise HttpError(f"Notion HTTP {r.status_code} : {desc[:200]}{hint}")

    async def send_text(self, http: Http, text: Text) -> None:
        return None          # messages système et résumés : Telegram / Discord seulement
