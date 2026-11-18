"""Mise en forme des alertes (texte commun aux deux canaux)."""
from __future__ import annotations

import html
from dataclasses import dataclass, field

from ..classify import Verdict
from ..models import Job

STYLE = {
    "TOP": ("🔥", "TOP MATCH", 0xE74C3C),
    "GOOD": ("✅", "BON MATCH", 0x2ECC71),
    "CHECK": ("❓", "DATES À VÉRIFIER", 0xF1C40F),
    "DIGEST": ("📋", "À SURVEILLER", 0x95A5A6),
}


@dataclass
class Alert:
    job: Job
    verdict: Verdict

    @property
    def emoji(self) -> str:
        return STYLE.get(self.verdict.tier, STYLE["DIGEST"])[0]

    @property
    def label(self) -> str:
        return STYLE.get(self.verdict.tier, STYLE["DIGEST"])[1]

    @property
    def color(self) -> int:
        return STYLE.get(self.verdict.tier, STYLE["DIGEST"])[2]

    def start_line(self) -> str:
        v = self.verdict
        labels = v.dates.start_labels if v.dates else []
        status = v.dates.start_status if v.dates else "UNKNOWN"
        if status == "PASS":
            extra = " (année déduite)" if "year_inferred" in v.flags else (" (période large)" if "partial" in v.flags else "")
            return f"🗓 Début : {labels[0] if labels else '?'}{extra} ✅"
        if status == "ASAP":
            return "🗓 Début : dès que possible ⚠️"
        if status == "FAIL":
            return f"🗓 Début : {labels[0] if labels else '?'} ❌"
        return "🗓 Début : non indiqué ❓"

    def duration_line(self) -> str:
        v = self.verdict
        if not v.dates or not v.dates.duration_label:
            return "⏱ Durée : non indiquée ❓"
        mark = {"PASS": "✅", "FAIL": "❌"}.get(v.dates.duration_status, "❓")
        return f"⏱ Durée : {v.dates.duration_label} {mark}"

    def desk_line(self) -> str:
        v = self.verdict
        return f"🎯 {', '.join(v.desks) if v.desks else 'Finance'} · score {v.score}/100"

    def source_line(self) -> str:
        name = self.job.source.split(":", 1)[-1]
        prov = self.job.extra.get("provider")
        return f"🔎 {prov or name}"

    def warning(self) -> str:
        v = self.verdict
        warns = []
        if v.tier == "CHECK":
            warns.append("Dates non précisées dans l'annonce : à vérifier avant de postuler.")
        if "alternance_possible" in v.flags:
            warns.append("Annonce « stage ou alternance ».")
        if "location_unknown" in v.flags:
            warns.append("Lieu non précisé.")
        return " ".join(warns)

    # ── Telegram (HTML) ────────────────────────────────────────────────────────────────────
    def telegram_html(self) -> str:
        e = html.escape
        j = self.job
        lines = [
            f"{self.emoji} <b>{e(j.title)}</b>",
            f"<i>{e(j.company)}</i>" + (f" · 📍 {e(j.location[:80])}" if j.location else ""),
            e(self.start_line()),
            e(self.duration_line()),
            e(self.desk_line()),
            e(self.source_line()),
        ]
        w = self.warning()
        if w:
            lines.append(f"⚠️ {e(w)}")
        return "\n".join(lines)

    # ── Discord (embed) ────────────────────────────────────────────────────────────────────
    def discord_embed(self) -> dict:
        j = self.job
        desc = [f"**{j.company}**" + (f" · 📍 {j.location[:80]}" if j.location else ""), self.start_line(),
                self.duration_line(), self.desk_line(), self.source_line()]
        w = self.warning()
        if w:
            desc.append(f"⚠️ {w}")
        embed = {
            "title": f"{self.emoji} {j.title}"[:250],
            "description": "\n".join(desc)[:3500],
            "color": self.color,
            "footer": {"text": f"{self.label}"},
        }
        if j.url:
            embed["url"] = j.url
        return embed


@dataclass
class Text:
    """Message système / digest : un titre, des lignes (déjà en texte brut)."""
    title: str
    lines: list[str] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)   # (libellé, url) listés en bas

    def telegram_html(self) -> str:
        e = html.escape
        out = [f"<b>{e(self.title)}</b>", ""] + [e(l) for l in self.lines]
        for label, url in self.links:
            out.append(f'• <a href="{html.escape(url, quote=True)}">{e(label)}</a>')
        return "\n".join(out)

    def discord_embed(self) -> dict:
        body = list(self.lines)
        for label, url in self.links:
            body.append(f"• [{label}]({url})")
        return {"title": self.title[:250], "description": "\n".join(body)[:3900], "color": 0x3498DB}
