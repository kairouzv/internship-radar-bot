import pytest


def test_perfect_match_is_top(clf, mkjob):
    j = mkjob(title="Stage M&A (H/F)", location="Paris, France",
              description="Start date: June 2027. Duration: 6 months. Join our M&A team.")
    v = clf.classify(j)
    assert v.tier == "TOP" and v.dates_status == "PASS" and v.score >= 85


def test_unknown_dates_is_check(clf, mkjob):
    v = clf.classify(mkjob(title="Leveraged Finance Intern", location="Paris", description="Great team."))
    assert v.tier == "CHECK" and v.dates_status == "UNKNOWN"


def test_wrong_dates_goes_to_digest(clf, mkjob):
    v = clf.classify(mkjob(title="Stage Corporate Finance", description="Début du stage : janvier 2027, 6 mois"))
    assert v.tier == "DIGEST" and v.dates_status == "FAIL"


def test_asap_goes_to_digest(clf, mkjob):
    v = clf.classify(mkjob(title="Stage M&A", description="Démarrage dès que possible"))
    assert v.tier == "DIGEST" and v.dates_status == "ASAP"


@pytest.mark.parametrize(
    "title,contract,expected",
    [
        ("Analyste M&A", "CDI", False),
        ("Alternance M&A", "", False),
        ("Stage ou alternance M&A", "", True),
        ("M&A Analyst", "Full-time", False),
        ("VIE Analyste M&A", "", False),
        ("M&A Analyst Programme Graduate", "", False),
        ("Analyste", "Intern", True),
        ("Summer Analyst – M&A", "", True),
    ],
)
def test_contract(clf, mkjob, title, contract, expected):
    ok, _, _ = clf.check_contract(mkjob(title=title, contract=contract))
    assert ok is expected


@pytest.mark.parametrize(
    "loc,expected",
    [
        ("Paris, France", True),
        ("La Défense", True),
        ("Genève, Suisse", True),
        ("Geneva", True),
        ("London", False),
        ("Paris, TX", False),
        ("", None),
        ("Frankfurt; Paris", True),
    ],
)
def test_location(clf, mkjob, loc, expected):
    assert clf.check_location(mkjob(location=loc, title="Intern")) is expected


def test_location_in_title(clf, mkjob):
    assert clf.check_location(mkjob(location="", title="Stage M&A Paris")) is True


def test_negative_title(clf, mkjob):
    v = clf.classify(mkjob(title="Stage Juriste M&A", description=""))
    assert v.tier in ("DIGEST", "DROP") and v.score < 60
    v = clf.classify(mkjob(title="Stage Data Engineer", description="finance"))
    assert v.tier == "DROP"


def test_description_only_signal_is_capped(clf, mkjob):
    v = clf.classify(mkjob(title="Stage Analyste", description="Au sein de l'équipe M&A, vous participerez aux deals."))
    assert 60 <= v.score <= 70  # notifiable mais jamais TOP


def test_generic_finance_digest_only(clf, mkjob):
    v = clf.classify(mkjob(title="Stage Analyste Financier", description=""))
    assert v.tier == "DIGEST"


def test_not_internship_dropped(clf, mkjob):
    v = clf.classify(mkjob(title="Associate M&A", contract="Full-time"))
    assert v.tier == "DROP"


def test_trusted_source_flag(clf, mkjob):
    j = mkjob(title="Analyste Corporate Finance", extra={"trust_internship": True}, description="")
    assert clf.classify(j).internship is True


def test_early_stage_is_not_internship(clf, mkjob):
    ok, _, _ = clf.check_contract(mkjob(title="Early-Stage Investor – Venture Capital"))
    assert ok is False
    ok, _, _ = clf.check_contract(mkjob(title="Management Trainee – Corporate Finance"))
    assert ok is False
