import asyncio
import json

import httpx

from radar.http import Http
from radar.notify import Alert, Text
from radar.notify.channels import Discord, Notifiers, Telegram, build_notifiers


def run(c):
    return asyncio.run(c)


def alert(clf, mkjob, **kw):
    j = mkjob(title="Stage M&A <H/F>", company="Banque & Cie", url="https://x/1", location="Paris",
              description="Start date: June 2027. Duration: 6 months.", **kw)
    return Alert(j, clf.classify(j))


def test_telegram_payload_and_escaping(clf, mkjob):
    seen = []

    def h(req):
        seen.append((str(req.url), json.loads(req.content)))
        return httpx.Response(200, json={"ok": True})
    http = Http(httpx.AsyncClient(transport=httpx.MockTransport(h)), retries=0)
    tg = Telegram("TOKEN", ["123", "456"])
    run(tg.send_alert(http, alert(clf, mkjob)))
    assert len(seen) == 2 and seen[0][0] == "https://api.telegram.org/botTOKEN/sendMessage"
    p = seen[0][1]
    assert p["parse_mode"] == "HTML" and "&lt;H/F&gt;" in p["text"] and "Banque &amp; Cie" in p["text"]
    assert "🗓 Début : juin 2027 ✅" in p["text"] and "⏱ Durée : 6 mois ✅" in p["text"]
    assert p["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://x/1"


def test_telegram_error_surfaces(clf, mkjob):
    http = Http(httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(401, json={"ok": False, "description": "Unauthorized"}))), retries=0)
    n = Notifiers([Telegram("BAD", ["1"])], pause=0)
    assert run(n.alert(http, alert(clf, mkjob))) is False
    assert "Unauthorized" in n.errors[0]


def test_telegram_splits_long_text():
    seen = []
    http = Http(httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: (seen.append(json.loads(r.content)), httpx.Response(200, json={"ok": True}))[1])), retries=0)
    t = Text("Digest", [f"ligne {i} " + "x" * 80 for i in range(120)])
    run(Telegram("T", ["1"]).send_text(http, t))
    assert len(seen) >= 3 and all(len(s["text"]) <= 4096 for s in seen)


def test_discord_embed(clf, mkjob):
    seen = []
    http = Http(httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: (seen.append(json.loads(r.content)), httpx.Response(204))[1])), retries=0)
    run(Discord("https://discord.com/api/webhooks/1/x").send_alert(http, alert(clf, mkjob)))
    e = seen[0]["embeds"][0]
    assert e["title"].startswith("🔥") and e["url"] == "https://x/1" and e["color"] == 0xE74C3C
    assert "Début : juin 2027" in e["description"]


def test_unknown_dates_message_warns(clf, mkjob):
    j = mkjob(title="Stage Private Equity", description="Join us")
    a = Alert(j, clf.classify(j))
    assert a.verdict.tier == "CHECK" and "non indiqué" in a.telegram_html() and "à vérifier" in a.telegram_html()


def test_build_notifiers_from_secrets():
    s = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1, 2", "DISCORD_WEBHOOK_URL": "https://d/x"}.get
    n = build_notifiers(lambda k: s(k, ""))
    assert n.names == ["telegram", "discord"] and n.channels[0].chat_ids == ["1", "2"]
    assert build_notifiers(lambda k: "").names == ["console"]
    assert build_notifiers(lambda k: s(k, ""), dry_run=True).names == ["console"]
