import asyncio
from datetime import date

import httpx
import pytest

from radar.config import Config
from radar.dedupe import DupIndex, canon_city, same_company, similar_titles, title_tokens
from radar.http import Http
from radar.notify.channels import Notifiers
from radar.pipeline import run_once
from radar.store import Store
from tests.conftest import make_job
from tests.test_pipeline import Capture, gh_job

TODAY = date(2026, 11, 18)


@pytest.mark.parametrize("a,b", [
    ("Paris, Île-de-France, France", "Paris"),
    ("Paris 8e", "Paris - La Défense"),
    ("Geneva", "Genève, Suisse"),
    ("La Défense", "Paris"),
])
def test_canon_city_same(a, b):
    assert canon_city(a) == canon_city(b)


@pytest.mark.parametrize("a,b", [
    ("Stage M&A (H/F)", "M&A Intern"),
    ("Mergers & Acquisitions Analyst Internship", "Stagiaire Fusions Acquisitions - Analyst"),
    ("Investment Banking Analyst Intern - M&A", "M&A Analyst Internship"),
    ("Summer 2027 M&A Analyst", "M&A Analyst - Summer 2027 Internship"),
    ("Stage Leveraged Finance 2027", "Leveraged Finance Intern 2027 (H/F)"),
    ("Private Equity Intern", "Stage Private Equity (H/F)"),
])
def test_same_offer(a, b):
    assert similar_titles(title_tokens(a), title_tokens(b))


@pytest.mark.parametrize("a,b", [
    ("Stage M&A", "Stage Leveraged Finance"),                  # desks différents
    ("Summer 2027 M&A Analyst", "Spring 2027 M&A Analyst"),    # saisons différentes
    ("Stage M&A 2027", "Stage M&A 2028"),                      # années différentes
    ("Stage Private Equity", "Stage Venture Capital"),
    ("Analyst Intern", "Credit Analyst Intern Risk"),          # trop générique pour être fusionné
    ("Stage Investor Relations", "Stage M&A"),
])
def test_different_offers(a, b):
    assert not similar_titles(title_tokens(a), title_tokens(b))


def test_same_company():
    assert same_company("bnpparibas", "bnpparibascib")
    assert same_company("lazard", "lazard")
    assert same_company("lazard", "lazardfreres")                     # préfixe accepté (garde-fous : titre + ville)
    assert not same_company("ing", "ingdiba")                         # clé trop courte pour un préfixe
    assert not same_company("", "lazard")


def test_index_match_and_city_guard():
    idx = DupIndex()
    idx.add(make_job(company="Lazard Frères", title="Stage M&A (H/F)", location="Paris, France"))
    assert idx.match(make_job(company="Lazard", title="M&A Intern", location="Paris"))
    assert not idx.match(make_job(company="Lazard", title="M&A Intern", location="Genève"))   # autre ville
    assert not idx.match(make_job(company="Rothschild", title="M&A Intern", location="Paris"))  # autre entreprise
    assert not idx.match(make_job(company="Lazard", title="Stage Leveraged Finance", location="Paris"))


def test_index_unknown_location_needs_exact_title():
    idx = DupIndex()
    idx.add(make_job(company="Lazard", title="Stage M&A", location=""))
    assert idx.match(make_job(company="Lazard", title="M&A Intern", location="Paris"))
    assert not idx.match(make_job(company="Lazard", title="Investment Banking M&A Intern", location="Paris"))


def test_from_store_ignores_dropped_and_dups():
    store = Store(":memory:")
    store.add_job(make_job(source_id="1", company="Lazard", title="Stage M&A"), "DROP", 0, None, False)
    store.add_job(make_job(source_id="2", company="Rothschild", title="Stage M&A"), "DUP", 0, None, False)
    store.add_job(make_job(source_id="3", company="Houlihan Lokey", title="Stage M&A"), "GOOD", 70, None, True)
    idx = DupIndex.from_store(store)
    assert not idx.match(make_job(company="Lazard", title="Stage M&A"))
    assert not idx.match(make_job(company="Rothschild", title="Stage M&A"))
    assert idx.match(make_job(company="Houlihan Lokey", title="M&A Intern"))


def test_pipeline_dedupes_differently_worded_offer_from_other_source(cfg):
    boards = {"a": [gh_job(1, "Stage M&A (H/F)")], "b": [gh_job(7, "M&A Analyst Internship")]}

    def handler(req):
        board = str(req.url).split("/boards/")[1].split("/")[0]
        return httpx.Response(200, json={"jobs": boards[board]})

    srcs = [{"name": "A", "type": "greenhouse", "board": "a", "company": "Lazard"},
            {"name": "B", "type": "greenhouse", "board": "b", "company": "Lazard Frères"}]
    cfg2 = Config(profile=cfg.profile, sources=srcs, dir=cfg.dir, env={})
    http = Http(httpx.AsyncClient(transport=httpx.MockTransport(handler)), retries=0)
    store, cap = Store(":memory:"), Capture()
    store.set_meta("init:greenhouse:A", "1")
    store.set_meta("init:greenhouse:B", "1")
    asyncio.run(run_once(cfg2, store, Notifiers([cap], pause=0), http, today=TODAY))
    assert len(cap.alerts) == 1
    # et au passage suivant, rien de plus
    asyncio.run(run_once(cfg2, store, Notifiers([cap], pause=0), http, today=TODAY))
    assert len(cap.alerts) == 1
