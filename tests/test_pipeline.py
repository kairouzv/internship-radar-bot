import asyncio
from datetime import date

import httpx

from radar.config import Config
from radar.http import Http
from radar.notify.channels import Channel, Notifiers
from radar.pipeline import run_once, send_digest
from radar.store import Store

TODAY = date(2026, 11, 18)


class Capture(Channel):
    name = "capture"

    def __init__(self, fail=False):
        self.alerts, self.texts, self.fail = [], [], fail

    async def send_alert(self, http, alert):
        if self.fail:
            raise RuntimeError("telegram down")
        self.alerts.append(alert)

    async def send_text(self, http, text):
        if self.fail:
            raise RuntimeError("telegram down")
        self.texts.append(text)


def gh_job(i, title, loc="Paris, France", content="Start date: June 2027. Duration: 6 months."):
    return {"id": i, "title": title, "absolute_url": f"https://gh/{i}", "location": {"name": loc},
            "departments": [], "content": content}


class World:
    """Faux Internet : liste mutable d'offres Greenhouse, avec option de panne."""

    def __init__(self):
        self.jobs = []
        self.down = False

    def handler(self, req):
        if self.down:
            return httpx.Response(500)
        return httpx.Response(200, json={"jobs": self.jobs})


def setup(cfg, world, sources=None):
    cfg2 = Config(profile=cfg.profile, sources=sources or [{"name": "Bank", "type": "greenhouse", "board": "bank",
                                                           "company": "Bank"}], dir=cfg.dir, env={})
    http = Http(httpx.AsyncClient(transport=httpx.MockTransport(world.handler)), retries=0)
    return cfg2, http


def run(coro):
    return asyncio.run(coro)


def test_first_run_is_silent_then_alerts_on_new(cfg):
    w = World()
    w.jobs = [gh_job(1, "Stage M&A"), gh_job(2, "Software Engineer Intern"), gh_job(3, "Stage Private Equity", content="Join us")]
    cfg2, http = setup(cfg, w)
    store, cap = Store(":memory:"), Capture()
    notif = Notifiers([cap], pause=0)

    r1 = run(run_once(cfg2, store, notif, http, today=TODAY))
    assert r1.alerts_sent == 0 and len(cap.alerts) == 0          # pas de rafale au premier passage
    assert len(cap.texts) == 1 and "Radar activé" in cap.texts[0].title
    assert any("Stage M&A" in l for l, _ in cap.texts[0].links)

    # rien de nouveau : silence total
    cap.texts.clear()
    r2 = run(run_once(cfg2, store, notif, http, today=TODAY))
    assert r2.alerts_sent == 0 and not cap.texts

    # nouvelle offre parfaite + une offre aux mauvaises dates
    w.jobs.append(gh_job(4, "Intern – Leveraged Finance"))
    w.jobs.append(gh_job(5, "Stage Corporate Finance", content="Start date: January 2027, 6 months"))
    r3 = run(run_once(cfg2, store, notif, http, today=TODAY))
    assert r3.alerts_sent == 1 and cap.alerts[0].job.title == "Intern – Leveraged Finance"
    assert cap.alerts[0].verdict.tier == "TOP"
    assert r3.digest_added == 1

    # même offre republiée sous un autre identifiant : pas de doublon
    w.jobs.append(gh_job(44, "Intern – Leveraged Finance"))
    cap.alerts.clear()
    run(run_once(cfg2, store, notif, http, today=TODAY))
    assert not cap.alerts


def test_cross_source_dedupe(cfg):
    w = World()
    w.jobs = [gh_job(1, "Stage M&A (H/F)")]
    srcs = [{"name": "A", "type": "greenhouse", "board": "a", "company": "Lazard"},
            {"name": "B", "type": "greenhouse", "board": "b", "company": "Lazard"}]
    cfg2, http = setup(cfg, w, srcs)
    store, cap = Store(":memory:"), Capture()
    store.set_meta("init:greenhouse:A", "1")
    store.set_meta("init:greenhouse:B", "1")
    run(run_once(cfg2, store, Notifiers([cap], pause=0), http, today=TODAY))
    assert len(cap.alerts) == 1


def test_notification_failure_retries_next_run(cfg):
    w = World()
    cfg2, http = setup(cfg, w)
    store = Store(":memory:")
    store.set_meta("init:greenhouse:Bank", "1")
    w.jobs = [gh_job(1, "Stage M&A")]
    bad = Capture(fail=True)
    r = run(run_once(cfg2, store, Notifiers([bad], pause=0), http, today=TODAY))
    assert r.alerts_sent == 0 and r.notify_errors
    good = Capture()
    r = run(run_once(cfg2, store, Notifiers([good], pause=0), http, today=TODAY))
    assert r.alerts_sent == 1 and len(good.alerts) == 1


def test_source_failure_alert_once_and_recovery(cfg):
    w = World()
    cfg2, http = setup(cfg, w)
    cfg2.profile = dict(cfg.profile, notifications={"source_failure_alert_after": 3, "max_alerts_per_run": 25})
    store, cap = Store(":memory:"), Capture()
    n = Notifiers([cap], pause=0)
    w.jobs = [gh_job(1, "Stage M&A")]
    run(run_once(cfg2, store, n, http, today=TODAY))      # ok -> bootstrap
    cap.texts.clear()
    w.down = True
    for _ in range(5):
        run(run_once(cfg2, store, n, http, today=TODAY))
    titles = [t.title for t in cap.texts]
    assert titles.count("⚠️ Source en panne") == 1
    w.down = False
    cap.texts.clear()
    run(run_once(cfg2, store, n, http, today=TODAY))
    assert [t.title for t in cap.texts] == ["✅ Source rétablie"]


def test_never_working_source_does_not_alert(cfg):
    w = World()
    w.down = True
    cfg2, http = setup(cfg, w)
    cap = Capture()
    store = Store(":memory:")
    for _ in range(10):
        run(run_once(cfg2, store, Notifiers([cap], pause=0), http, today=TODAY))
    assert not cap.texts


def test_deferred_until_detail_available(cfg):
    """Workday-like : la liste n'a pas de description ; si la fiche échoue 3 fois, on classe quand même."""
    state = {"fail": True}

    def h(req):
        if req.method == "POST":
            return httpx.Response(200, json={"total": 1, "jobPostings": [{
                "title": "Stage M&A", "externalPath": "/job/Paris/x_R1", "locationsText": "Paris", "bulletFields": ["R1"]}]})
        if state["fail"]:
            return httpx.Response(500)
        return httpx.Response(200, json={"jobPostingInfo": {"jobDescription": "Start date: June 2027, 6 months", "location": "Paris"}})
    cfg2 = Config(profile=cfg.profile, sources=[{"name": "W", "type": "workday", "host": "w.wd5.myworkdayjobs.com", "site": "S",
                                                 "search": ["stage"]}], dir=cfg.dir, env={})
    http = Http(httpx.AsyncClient(transport=httpx.MockTransport(h)), retries=0)
    store, cap = Store(":memory:"), Capture()
    store.set_meta("init:workday:W", "1")
    n = Notifiers([cap], pause=0)
    r = run(run_once(cfg2, store, n, http, today=TODAY))
    assert r.deferred == 1 and not cap.alerts
    state["fail"] = False
    r = run(run_once(cfg2, store, n, http, today=TODAY))
    assert len(cap.alerts) == 1 and cap.alerts[0].verdict.tier == "TOP"


def test_alert_cap_overflow(cfg):
    w = World()
    cfg2, http = setup(cfg, w)
    cfg2.profile = dict(cfg.profile, notifications={"max_alerts_per_run": 2, "source_failure_alert_after": 6})
    store, cap = Store(":memory:"), Capture()
    store.set_meta("init:greenhouse:Bank", "1")
    w.jobs = [gh_job(i, f"Stage M&A {i}") for i in range(5)]
    r = run(run_once(cfg2, store, Notifiers([cap], pause=0), http, today=TODAY))
    assert r.alerts_sent == 2 and len(cap.alerts) == 2
    assert any("Trop d'offres" in t.title for t in cap.texts)


def test_digest_lists_near_misses(cfg):
    w = World()
    cfg2, http = setup(cfg, w)
    store, cap = Store(":memory:"), Capture()
    n = Notifiers([cap], pause=0)
    store.set_meta("init:greenhouse:Bank", "1")
    w.jobs = [gh_job(1, "Stage Corporate Finance", content="Début du stage : janvier 2027, 6 mois"), gh_job(2, "Stage M&A")]
    run(run_once(cfg2, store, n, http, today=TODAY))
    cap.texts.clear()
    t = run(send_digest(cfg2, store, n, http))
    body = "\n".join(t.lines)
    assert "1 alerte" in body and "Stage Corporate Finance" in body and "janvier 2027" in body
    # le résumé ne se répète pas
    t2 = run(send_digest(cfg2, store, n, http))
    assert "Stage Corporate Finance" not in "\n".join(t2.lines)


def test_every_minutes_throttles_source(cfg):
    w = World()
    calls = {"n": 0}
    base = w.handler

    def h(req):
        calls["n"] += 1
        return base(req)
    cfg2 = Config(profile=cfg.profile, sources=[{"name": "Slow", "type": "greenhouse", "board": "s", "every_minutes": 60}],
                  dir=cfg.dir, env={})
    http = Http(httpx.AsyncClient(transport=httpx.MockTransport(h)), retries=0)
    store, cap = Store(":memory:"), Capture()
    n = Notifiers([cap], pause=0)
    run(run_once(cfg2, store, n, http, today=TODAY))
    run(run_once(cfg2, store, n, http, today=TODAY))
    assert calls["n"] == 1
