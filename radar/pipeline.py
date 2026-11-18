"""Orchestration : collecte -> dédoublonnage -> enrichissement -> classement -> alertes."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from .classify import Classifier, Verdict
from .config import Config
from .http import Http
from .models import Job
from .notify import Alert, Notifiers, Text
from .sources import build_sources
from .sources.base import Source
from .store import Store

ALERT_TIERS = ("TOP", "GOOD", "CHECK")
SOURCE_TIMEOUT = 120.0


@dataclass
class RunReport:
    fetched: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    new_jobs: int = 0
    candidates: int = 0
    alerts_sent: int = 0
    digest_added: int = 0
    deferred: int = 0
    bootstrap_sources: list[str] = field(default_factory=list)
    notify_errors: list[str] = field(default_factory=list)
    seconds: float = 0.0
    requests: int = 0

    def summary(self) -> str:
        ok = len(self.fetched)
        return (f"{ok} sources OK, {len(self.errors)} en erreur, {len(self.skipped)} ignorées · "
                f"{sum(self.fetched.values())} offres lues · {self.new_jobs} nouvelles · {self.candidates} candidates · "
                f"{self.alerts_sent} alertes · {self.digest_added} pour le résumé · {self.deferred} reportées · "
                f"{self.requests} requêtes · {self.seconds:.0f}s")


def _summary_of(v: Verdict) -> dict:
    d = v.dates
    return {
        "reasons": v.reasons[:8], "desks": v.desks, "flags": v.flags,
        "start": d.start_labels[:2] if d else [], "duration": d.duration_label if d else "",
        "dates": v.dates_status,
    }


async def _fetch_one(src: Source, http: Http) -> list[Job]:
    return await asyncio.wait_for(src.fetch(http), timeout=SOURCE_TIMEOUT)


async def collect(sources: list[Source], http: Http, report: RunReport) -> dict[str, list[Job]]:
    """Interroge toutes les sources en parallèle. Une source qui plante n'arrête pas les autres."""
    results = await asyncio.gather(*[_fetch_one(s, http) for s in sources], return_exceptions=True)
    out: dict[str, list[Job]] = {}
    for src, res in zip(sources, results):
        if isinstance(res, BaseException):
            msg = f"{type(res).__name__}: {res}" if str(res) else type(res).__name__
            report.errors[src.key] = msg[:300]
        else:
            out[src.key] = res
            report.fetched[src.key] = len(res)
    return out


def _title_rank(clf: Classifier, job: Job) -> int:
    return clf.score_role(job)[0]


async def _enrich_many(srcs: dict[str, Source], http: Http, jobs: list[Job], cap: int) -> int:
    todo = [j for j in jobs if j.needs_detail][:cap]
    sem = asyncio.Semaphore(8)

    async def one(j: Job) -> None:
        async with sem:
            try:
                await asyncio.wait_for(srcs[j.source].enrich(http, j), timeout=45)
            except Exception as e:  # noqa: BLE001
                j.extra["detail_error"] = str(e)[:120]

    await asyncio.gather(*[one(j) for j in todo])
    return len(todo)


async def run_once(
    cfg: Config,
    store: Store,
    notifiers: Notifiers,
    http: Http,
    *,
    only: str | None = None,
    today: date | None = None,
    max_enrich: int = 80,
) -> RunReport:
    t0 = time.monotonic()
    report = RunReport()
    clf = Classifier(cfg.profile, today=today)
    notif_cfg = cfg.profile.get("notifications", {})
    max_alerts = int(notif_cfg.get("max_alerts_per_run", 25))
    fail_after = int(notif_cfg.get("source_failure_alert_after", 6))

    # 1. sources actives
    sources: list[Source] = []
    for s in build_sources(cfg.sources, cfg.secret):
        if only and only.lower() not in s.key.lower():
            continue
        miss = s.missing()
        if miss:
            report.skipped[s.key] = miss
            continue
        every = float(s.cfg.get("every_minutes", 0) or 0)
        if every and not only:
            last = store.get_meta(f"last:{s.key}")
            if last:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() / 60
                if age < every * 0.9:
                    continue                      # pas encore l'heure de réinterroger cette source
        if s.uses_store:
            s.bind(store)
        sources.append(s)
    by_key = {s.key: s for s in sources}

    # 2. collecte
    raw = await collect(sources, http, report)

    for key in list(raw) + list(report.errors):
        if by_key[key].cfg.get("every_minutes"):
            store.set_meta(f"last:{key}", datetime.now(timezone.utc).isoformat())

    # 3. santé des sources
    system_msgs: list[Text] = []
    for key, err in report.errors.items():
        if store.record_fail(key, err, fail_after) == "failing":
            system_msgs.append(Text("⚠️ Source en panne", [f"{key} échoue depuis {fail_after} passages.", f"Dernière erreur : {err}"]))
    for key, jobs in raw.items():
        state = store.record_ok(key, len(jobs))
        if state == "recovered":
            system_msgs.append(Text("✅ Source rétablie", [f"{key} fonctionne de nouveau."]))
        h = store.health(key)
        if h and h["max_count"] >= 5 and h["zero_streak"] == 3 and len(jobs) == 0:
            system_msgs.append(Text("⚠️ Source suspecte", [
                f"{key} renvoie 0 offre depuis 3 passages (avant : jusqu'à {h['max_count']}).",
                "Le site a peut-être changé : lance « radar doctor » pour vérifier."]))

    # 4. nouveaux candidats (stage + lieu plausible), dédoublonnés
    run_fps: set[str] = set()
    fresh: list[tuple[str, Job]] = []
    for key, jobs in raw.items():
        first_time = store.get_meta(f"init:{key}") != "1"
        for j in jobs:
            if store.known_uid(j.uid):
                continue
            ok, _, _ = clf.check_contract(j)
            if not ok or clf.check_location(j) is False:
                continue
            if store.known_fp(j.fingerprint) or j.fingerprint in run_fps:
                store.add_job(j, "DUP", 0, None, False)
                continue
            run_fps.add(j.fingerprint)
            fresh.append((key, j))
            report.new_jobs += 1
        if first_time:
            store.set_meta(f"init:{key}", "1")
            report.bootstrap_sources.append(key)
    bootstrap_keys = set(report.bootstrap_sources)

    # 5. enrichissement (descriptions complètes) — priorité aux titres les plus pertinents
    fresh.sort(key=lambda kj: _title_rank(clf, kj[1]), reverse=True)
    fresh_jobs = [j for _, j in fresh]
    await _enrich_many(by_key, http, fresh_jobs, max_enrich)

    # 6. classement (une offre dont le détail n'a pas pu être chargé est reportée au passage suivant)
    alerts: list[Alert] = []
    boot_alerts: list[Alert] = []
    for key, j in fresh:
        if j.needs_detail:
            failed = bool(j.extra.get("detail_error"))
            if not failed or store.bump(f"detail_fail:{j.uid}") < 3:
                report.deferred += 1
                continue
        v = clf.classify(j)
        report.candidates += 1
        if v.tier in ALERT_TIERS:
            (boot_alerts if key in bootstrap_keys else alerts).append(Alert(j, v))
        elif v.tier == "DIGEST":
            store.add_job(j, "DIGEST", v.score, _summary_of(v), False, digested=key in bootstrap_keys)
            report.digest_added += 0 if key in bootstrap_keys else 1
        else:
            store.add_job(j, "DROP", v.score, None, False)

    rank = {"TOP": 0, "GOOD": 1, "CHECK": 2}
    alerts.sort(key=lambda a: (rank[a.verdict.tier], -a.verdict.score))
    boot_alerts.sort(key=lambda a: (rank[a.verdict.tier], -a.verdict.score))

    # 7. premier passage d'une source : un seul message récapitulatif, pas une rafale
    if boot_alerts:
        for a in boot_alerts:
            store.add_job(a.job, a.verdict.tier, a.verdict.score, _summary_of(a.verdict), False)
        n = int(notif_cfg.get("bootstrap_top_n", 10))
        lines = [f"{len(boot_alerts)} offre(s) déjà ouverte(s) correspondent à ton profil (sources : "
                 f"{', '.join(sorted(bootstrap_keys))}). Les plus pertinentes :"]
        links = []
        for a in boot_alerts[:n]:
            tag = {"TOP": "🔥", "GOOD": "✅", "CHECK": "❓"}[a.verdict.tier]
            links.append((f"{tag} {a.job.title} — {a.job.company} ({a.job.location or '?'})", a.job.url))
        await notifiers.text(http, Text("🚀 Radar activé", lines, links))

    # 8. alertes immédiates
    sent = 0
    for a in alerts:
        if sent >= max_alerts:
            store.add_job(a.job, "DIGEST", a.verdict.score, _summary_of(a.verdict), False)
            report.digest_added += 1
            continue
        if await notifiers.alert(http, a):
            store.add_job(a.job, a.verdict.tier, a.verdict.score, _summary_of(a.verdict), True)
            sent += 1
        # échec d'envoi : on n'enregistre pas -> nouvelle tentative au prochain passage
    report.alerts_sent = sent
    if len(alerts) > max_alerts:
        await notifiers.text(http, Text("📋 Trop d'offres d'un coup", [
            f"{len(alerts) - max_alerts} offre(s) supplémentaire(s) sont dans le résumé du matin."]))

    # 9. messages système (pannes / rétablissements)
    for m in system_msgs:
        await notifiers.text(http, m)

    report.notify_errors = list(notifiers.errors)
    store.prune()
    store.commit()
    report.seconds = time.monotonic() - t0
    report.requests = http.request_count
    return report


# ─────────────────────────────────────────────────────────────────────────────────────────────
# Résumé quotidien
# ─────────────────────────────────────────────────────────────────────────────────────────────

async def send_digest(cfg: Config, store: Store, notifiers: Notifiers, http: Http, hours: int = 24) -> Text:
    import json

    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    alerts = store.recent_alerts(since)
    near = store.digest_items(since)
    lines: list[str] = []
    links: list[tuple[str, str]] = []

    lines.append(f"Dernières {hours} h : {len(alerts)} alerte(s) envoyée(s), {len(near)} offre(s) à surveiller.")
    if alerts:
        lines.append("")
        lines.append("Alertes récentes :")
        for r in alerts[:8]:
            tag = {"TOP": "🔥", "GOOD": "✅", "CHECK": "❓"}.get(r["tier"], "•")
            links.append((f"{tag} {r['title']} — {r['company']}", r["url"]))
    if near:
        lines.append("")
        lines.append("À surveiller (pertinentes mais dates/durée hors fenêtre, ou métier moins précis) :")
        for r in near[:12]:
            s = json.loads(r["summary"] or "{}")
            why = next((x for x in s.get("reasons", []) if x.startswith(("début", "durée"))), "") or "métier moins précis"
            lines.append(f"• {r['title']} — {r['company']} → {why}")
            links.append((f"{r['title']} — {r['company']}", r["url"]))
        if len(near) > 12:
            lines.append(f"… et {len(near) - 12} autre(s).")

    health = store.all_health()
    bad = [h for h in health if h["consecutive_failures"] >= 3 and h["ok_runs"] > 0]
    never = [h for h in health if h["ok_runs"] == 0]
    if bad or never:
        lines.append("")
        lines.append("État des sources :")
        for h in bad:
            lines.append(f"⚠️ {h['source']} : en échec ({h['consecutive_failures']} passages) — {h['last_error'] or ''}"[:200])
        if never:
            lines.append(f"ℹ️ {len(never)} source(s) n'ont jamais répondu : {', '.join(h['source'] for h in never[:6])}"
                         f"{'…' if len(never) > 6 else ''} (lance « radar doctor »).")
    ok_n = sum(1 for h in health if h["ok_runs"] > 0 and h["consecutive_failures"] == 0)
    lines.append("")
    lines.append(f"Sources opérationnelles : {ok_n}/{len(health)}.")

    text = Text("☀️ Radar stages — résumé du jour", lines, links)
    if await notifiers.text(http, text):
        store.mark_digested([r["uid"] for r in near])
        store.commit()
    return text
