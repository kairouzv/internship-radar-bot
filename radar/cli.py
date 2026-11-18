"""Ligne de commande : radar run | digest | doctor | discover | explain | test-notify | telegram-chat-id."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

from . import __version__
from .classify import Classifier
from .config import ConfigError, load_config
from .http import Http
from .models import Job
from .notify import Alert, Text, build_notifiers
from .pipeline import run_once, send_digest
from .sources import build_sources
from .sources.discover import discover, to_yaml
from .store import Store


def _load_dotenv(path: Path = Path(".env")) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _cfg(args):
    env = dict(_load_dotenv())
    env.update(os.environ)
    return load_config(args.config, env=env)


def _state_path(args) -> str:
    return args.state or os.environ.get("RADAR_STATE") or "state/radar.db"


def _step_summary(md: str) -> None:
    p = os.environ.get("GITHUB_STEP_SUMMARY")
    if p:
        with open(p, "a", encoding="utf-8") as f:
            f.write(md + "\n")


# ── run ──────────────────────────────────────────────────────────────────────────────────────
async def cmd_run(args) -> int:
    cfg = _cfg(args)
    notifiers = build_notifiers(cfg.secret, dry_run=args.dry_run)
    if not args.dry_run and not notifiers.real:
        print("✗ Aucun canal de notification configuré : définis TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID "
              "et/ou DISCORD_WEBHOOK_URL (secrets GitHub ou fichier .env).\n  Pour simuler sans rien envoyer : "
              "python -m radar run --dry-run", file=sys.stderr)
        return 1
    state = _state_path(args)
    tmp = None
    if args.dry_run:
        # simulation : on travaille sur une copie de l'état, la vraie base n'est jamais modifiée
        if args.fresh or not Path(state).is_file():
            state = ":memory:"
        else:
            import shutil
            import tempfile
            tmp = tempfile.mkdtemp()
            shutil.copy(state, Path(tmp) / "radar.db")
            state = str(Path(tmp) / "radar.db")
    store = Store(state)
    http = Http()
    try:
        rep = await run_once(cfg, store, notifiers, http, only=args.only)
    finally:
        await http.aclose()
        store.close()
    print(rep.summary())
    for k, e in rep.errors.items():
        print(f"  ✗ {k}: {e}")
    for k, e in rep.skipped.items():
        print(f"  - {k}: ignorée ({e})")
    for e in rep.notify_errors:
        print(f"  ! notification : {e}")
    _step_summary(f"### Radar\n{rep.summary()}\n" + "\n".join(f"- ✗ `{k}` {e}" for k, e in rep.errors.items()))
    real = "console" not in notifiers.names
    return 1 if (rep.notify_errors and real) else 0


# ── digest ───────────────────────────────────────────────────────────────────────────────────
async def cmd_digest(args) -> int:
    cfg = _cfg(args)
    notifiers = build_notifiers(cfg.secret, dry_run=args.dry_run)
    store = Store(_state_path(args))
    http = Http()
    try:
        await send_digest(cfg, store, notifiers, http, hours=args.hours)
    finally:
        await http.aclose()
        store.close()
    for e in notifiers.errors:
        print(f"  ! notification : {e}")
    return 1 if notifiers.errors and "console" not in notifiers.names else 0


# ── doctor ───────────────────────────────────────────────────────────────────────────────────
async def cmd_doctor(args) -> int:
    cfg = _cfg(args)
    clf = Classifier(cfg.profile)
    store = Store(":memory:")
    http = Http()
    sources = [s for s in build_sources(cfg.sources, cfg.secret) if not args.only or args.only.lower() in s.key.lower()]

    async def probe(s):
        miss = s.missing()
        if miss:
            return s, "SKIP", 0, 0, 0, 0, [], miss, 0.0
        if s.uses_store:
            s.bind(store)
        t0 = time.monotonic()
        try:
            jobs = await asyncio.wait_for(s.fetch(http), timeout=120)
        except Exception as e:  # noqa: BLE001
            return s, "FAIL", 0, 0, 0, 0, [], f"{type(e).__name__}: {e}"[:160], time.monotonic() - t0
        stages = [j for j in jobs if clf.check_contract(j)[0]]
        loc = [j for j in stages if clf.check_location(j) is not False]
        rel = [j for j in loc if clf.score_role(j)[0] >= clf.t_digest]
        return s, "OK", len(jobs), len(stages), len(loc), len(rel), rel[:3], "", time.monotonic() - t0

    try:
        rows = await asyncio.gather(*[probe(s) for s in sources])
    finally:
        await http.aclose()
    print(f"{'ÉTAT':5} {'SOURCE':34} {'OFFRES':>6} {'STAGES':>6} {'LIEU':>5} {'PERTIN.':>7} {'TEMPS':>6}  DÉTAIL")
    bad = 0
    for s, st, n, ns, nl, nr, sample, msg, dt in sorted(rows, key=lambda r: (r[1] != "FAIL", r[0].key)):
        if st == "FAIL":
            bad += 1
        flag = {"OK": "OK", "FAIL": "FAIL", "SKIP": "SKIP"}[st]
        print(f"{flag:5} {s.key[:34]:34} {n:>6} {ns:>6} {nl:>5} {nr:>7} {dt:>5.1f}s  {msg}")
        for j in sample:
            print(f"        ↳ {j.title[:70]} — {j.location[:30]}")
    print(f"\n{len(rows) - bad}/{len(rows)} sources opérationnelles. ({http.request_count} requêtes)")
    if args.json:
        Path(args.json).write_text(json.dumps(
            [{"source": r[0].key, "status": r[1], "jobs": r[2], "internships": r[3], "location_ok": r[4], "relevant": r[5],
              "error": r[7], "seconds": round(r[8], 2)} for r in rows], ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if bad else 0


# ── discover ─────────────────────────────────────────────────────────────────────────────────
async def cmd_discover(args) -> int:
    http = Http()
    all_sugg: list[dict] = []
    try:
        for i, url in enumerate(args.urls):
            name = args.name[i] if args.name and i < len(args.name) else url.split("//")[-1].split("/")[0].replace("www.", "")
            try:
                res = await discover(http, url, name)
            except Exception as e:  # noqa: BLE001
                print(f"✗ {url} : {e}")
                continue
            print(f"\n# {url} (HTTP {res['status']})")
            if res["suggestions"]:
                print(to_yaml(res["suggestions"]))
                all_sugg.extend(res["suggestions"])
            for n in res["notes"]:
                print(f"# ℹ️ {n}")
    finally:
        await http.aclose()
    return 0 if all_sugg else 1


# ── explain ──────────────────────────────────────────────────────────────────────────────────
async def cmd_explain(args) -> int:
    cfg = _cfg(args)
    text = args.text or ""
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif not text and not sys.stdin.isatty():
        text = sys.stdin.read()
    j = Job(source="explain:cli", source_id="1", title=args.title, company=args.company, url="", location=args.location,
            description=text, contract=args.contract)
    v = Classifier(cfg.profile).classify(j)
    print(f"Verdict : {v.tier}  (score métier {v.score}, dates {v.dates_status})")
    for r in v.reasons:
        print(f"  • {r}")
    if v.flags:
        print(f"  drapeaux : {', '.join(v.flags)}")
    return 0


# ── test-notify ──────────────────────────────────────────────────────────────────────────────
async def cmd_test_notify(args) -> int:
    cfg = _cfg(args)
    notifiers = build_notifiers(cfg.secret)
    http = Http()
    try:
        j = Job(source="test:demo", source_id="1", title="Stage M&A – Paris (H/F)", company="Banque Démo",
                url="https://example.com/offre", location="Paris, France",
                description="Start date: June 2027. Duration: 6 months. M&A advisory team.")
        v = Classifier(cfg.profile).classify(j)
        ok1 = await notifiers.alert(http, Alert(j, v))
        ok2 = await notifiers.text(http, Text("✅ Test Radar", ["Si tu lis ceci, les notifications fonctionnent.",
                                                              f"Canaux actifs : {', '.join(notifiers.names)}"]))
    finally:
        await http.aclose()
    for e in notifiers.errors:
        print(f"✗ {e}")
    print(f"Canaux : {', '.join(notifiers.names)} → {'OK' if ok1 and ok2 and not notifiers.errors else 'ÉCHEC'}")
    return 0 if ok1 and ok2 and not notifiers.errors else 1


# ── telegram-chat-id ─────────────────────────────────────────────────────────────────────────
async def cmd_chat_id(args) -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or _load_dotenv().get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("Définis TELEGRAM_BOT_TOKEN (variable d'environnement ou fichier .env) puis écris un message à ton bot.")
        return 1
    http = Http()
    try:
        data = await http.get_json(f"https://api.telegram.org/bot{token}/getUpdates")
    finally:
        await http.aclose()
    chats = {}
    for u in data.get("result", []):
        m = u.get("message") or u.get("channel_post") or u.get("my_chat_member", {})
        c = m.get("chat")
        if c:
            chats[c["id"]] = c.get("title") or c.get("username") or c.get("first_name") or "?"
    if not chats:
        print("Aucun message reçu. Envoie « /start » à ton bot dans Telegram, puis relance cette commande.")
        return 1
    for cid, name in chats.items():
        print(f"TELEGRAM_CHAT_ID={cid}    ({name})")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="radar", description="Radar de stages finance (Paris / Genève).")
    ap.add_argument("--version", action="version", version=f"radar {__version__}")
    ap.add_argument("--config", help="dossier de configuration (défaut : ./config)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="un passage : collecte, filtre, alerte")
    p.add_argument("--dry-run", action="store_true", help="affiche dans le terminal, n'envoie rien")
    p.add_argument("--fresh", action="store_true", help="avec --dry-run : état vide, rien n'est enregistré")
    p.add_argument("--only", help="ne traiter que les sources dont le nom contient ce texte")
    p.add_argument("--state", help="chemin de la base d'état (défaut : state/radar.db)")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("digest", help="envoie le résumé quotidien")
    p.add_argument("--hours", type=int, default=24)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--state")
    p.set_defaults(fn=cmd_digest)

    p = sub.add_parser("doctor", help="teste chaque source et dit ce qu'elle remonte")
    p.add_argument("--only")
    p.add_argument("--json", help="écrit le rapport dans ce fichier JSON")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("discover", help="détecte l'ATS d'une page carrières et génère la config")
    p.add_argument("urls", nargs="+")
    p.add_argument("--name", action="append", help="nom de la source (répétable, dans l'ordre des URLs)")
    p.set_defaults(fn=cmd_discover)

    p = sub.add_parser("explain", help="montre comment une offre serait classée (dates, métier, lieu)")
    p.add_argument("--title", required=True)
    p.add_argument("--company", default="Entreprise")
    p.add_argument("--location", default="Paris")
    p.add_argument("--contract", default="")
    p.add_argument("--text", help="texte de l'annonce (ou --file, ou stdin)")
    p.add_argument("--file")
    p.set_defaults(fn=cmd_explain)

    p = sub.add_parser("test-notify", help="envoie un message de test sur Telegram / Discord")
    p.set_defaults(fn=cmd_test_notify)

    p = sub.add_parser("telegram-chat-id", help="affiche ton TELEGRAM_CHAT_ID après un « /start » au bot")
    p.set_defaults(fn=cmd_chat_id)

    args = ap.parse_args(argv)
    try:
        return asyncio.run(args.fn(args))
    except ConfigError as e:
        print(f"Configuration invalide : {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
