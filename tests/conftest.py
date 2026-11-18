from datetime import date
from pathlib import Path

import pytest

from radar.classify import Classifier
from radar.config import load_config
from radar.models import Job

TODAY = date(2026, 11, 18)
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


@pytest.fixture(scope="session")
def cfg():
    return load_config(CONFIG_DIR, env={})


@pytest.fixture()
def clf(cfg):
    return Classifier(cfg.profile, today=TODAY)


def make_job(**kw) -> Job:
    base = dict(source="test:x", source_id="1", title="Stage M&A", company="Acme Bank", url="https://x/1", location="Paris")
    base.update(kw)
    return Job(**base)


@pytest.fixture()
def mkjob():
    return make_job
