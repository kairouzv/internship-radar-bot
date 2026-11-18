"""Canaux de notification : Telegram, Discord, console."""
from __future__ import annotations

import asyncio

from ..http import Http, HttpError
from .format import Alert, Text


class Channel:
    name = "channel"

    async def send_alert(self, http: Http, alert: Alert) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    async def send_text(self, http: Http, text: Text) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class Telegram(Channel):
    name = "telegram"
    LIMIT = 3900

    def __init__(self, token: str, chat_ids: list[str], thread_id: str = ""):
        self.token, self.chat_ids, self.thread_id = token, chat_ids, thread_id

    async def _send(self, http: Http, chat_id: str, html_text: str, url: str | None = None, button: str = "Voir l'offre") -> None:
        payload: dict = {"chat_id": chat_id, "text": html_text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if self.thread_id:
            payload["message_thread_id"] = int(self.thread_id)
        if url:
            payload["reply_markup"] = {"inline_keyboard": [[{"text": f"🔗 {button}", "url": url}]]}
        r = await http.request("POST", f"https://api.telegram.org/bot{self.token}/sendMessage", json=payload)
        if r.status_code != 200:
            try:
                desc = r.json().get("description", "")
            except ValueError:
                desc = r.text[:120]
            raise HttpError(f"Telegram HTTP {r.status_code} : {desc}")

    async def send_alert(self, http: Http, alert: Alert) -> None:
        for cid in self.chat_ids:
            await self._send(http, cid, alert.telegram_html(), alert.job.url or None, "Voir l'offre")

    async def send_text(self, http: Http, text: Text) -> None:
        body = text.telegram_html()
        chunks, cur = [], ""
        for line in body.split("\n"):
            if len(cur) + len(line) + 1 > self.LIMIT:
                chunks.append(cur)
                cur = ""
            cur += line + "\n"
        if cur.strip():
            chunks.append(cur)
        for cid in self.chat_ids:
            for c in chunks:
                await self._send(http, cid, c.rstrip())


class Discord(Channel):
    name = "discord"

    def __init__(self, webhook_url: str):
        self.url = webhook_url

    async def _post(self, http: Http, embeds: list[dict]) -> None:
        r = await http.request("POST", self.url, json={"username": "Internship Radar", "embeds": embeds})
        if r.status_code not in (200, 204):
            raise HttpError(f"Discord HTTP {r.status_code} : {r.text[:120]}")

    async def send_alert(self, http: Http, alert: Alert) -> None:
        await self._post(http, [alert.discord_embed()])

    async def send_text(self, http: Http, text: Text) -> None:
        await self._post(http, [text.discord_embed()])


class Console(Channel):
    """Affichage terminal (mode --dry-run ou aucun canal configuré)."""
    name = "console"

    async def send_alert(self, http: Http, alert: Alert) -> None:
        j = alert.job
        print(f"\n[{alert.label}] {j.title} — {j.company} ({j.location})\n  {alert.start_line()} | {alert.duration_line()}\n"
              f"  {alert.desk_line()}\n  {j.url}")

    async def send_text(self, http: Http, text: Text) -> None:
        print(f"\n== {text.title} ==")
        for l in text.lines:
            print(l)
        for label, url in text.links:
            print(f"  - {label}: {url}")


class Notifiers:
    """Diffuse sur tous les canaux ; l'échec d'un canal n'empêche pas les autres."""

    def __init__(self, channels: list[Channel], pause: float = 1.1):
        self.channels = channels
        self.pause = pause
        self.errors: list[str] = []

    @property
    def names(self) -> list[str]:
        return [c.name for c in self.channels]

    @property
    def real(self) -> bool:
        """Au moins un vrai canal (Telegram / Discord) est configuré."""
        return any(not isinstance(c, Console) for c in self.channels)

    async def alert(self, http: Http, alert: Alert) -> bool:
        ok = False
        for c in self.channels:
            try:
                await c.send_alert(http, alert)
                ok = True
            except Exception as e:  # noqa: BLE001
                self.errors.append(f"{c.name}: {e}")
        if self.pause and not isinstance(self.channels[0] if self.channels else None, Console):
            await asyncio.sleep(self.pause)
        return ok

    async def text(self, http: Http, text: Text) -> bool:
        ok = False
        for c in self.channels:
            try:
                await c.send_text(http, text)
                ok = True
            except Exception as e:  # noqa: BLE001
                self.errors.append(f"{c.name}: {e}")
        return ok


def build_notifiers(secret, *, dry_run: bool = False, pause: float = 1.1) -> Notifiers:
    if dry_run:
        return Notifiers([Console()], pause=0)
    channels: list[Channel] = []
    token, chats = secret("TELEGRAM_BOT_TOKEN"), secret("TELEGRAM_CHAT_ID")
    if token and chats:
        channels.append(Telegram(token, [c.strip() for c in chats.split(",") if c.strip()], secret("TELEGRAM_THREAD_ID")))
    hook = secret("DISCORD_WEBHOOK_URL")
    if hook:
        channels.append(Discord(hook))
    if not channels:
        channels.append(Console())
    return Notifiers(channels, pause=pause)
