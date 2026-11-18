from datetime import date

import pytest

from radar.extract import evaluate_dates, extract, extract_duration
from radar.textutil import norm

REF = date(2026, 11, 18)
WINDOW = {
    "earliest_start": date(2027, 4, 15),
    "latest_start": date(2027, 8, 31),
    "min_months": 4,
    "max_months": 6,
}


def starts(title="", desc="", hint=""):
    ex = extract(title, desc, hint, ref=REF)
    return [(m.start, m.end) for m in ex.starts]


def first_start(title="", desc="", hint=""):
    s = starts(title, desc, hint)
    return s[0][0] if s else None


@pytest.mark.parametrize(
    "desc,expected",
    [
        ("Start date: June 2027", date(2027, 6, 1)),
        ("Starting in May 2027 for a period of 6 months", date(2027, 5, 1)),
        ("Stage de 6 mois à partir de mai 2027", date(2027, 5, 1)),
        ("Date de début : 1er juin 2027", date(2027, 6, 1)),
        ("Démarrage : septembre 2027", date(2027, 9, 1)),
        ("Prise de poste dès juillet 2027", date(2027, 7, 1)),
        ("Start date: 01/06/2027", date(2027, 6, 1)),
        ("Start: 2027-05-03", date(2027, 5, 3)),
        ("Beginning on June 1st, 2027", date(2027, 6, 1)),
        ("Internship starting in April", date(2027, 4, 1)),            # année déduite
        ("Stage à partir de janvier", date(2027, 1, 1)),
        ("Début du stage : début juin 2027", date(2027, 6, 1)),
        ("Start date: mid-May 2027", date(2027, 5, 11)),
        ("Start date: end of June 2027", date(2027, 6, 21)),
        ("Beginn: ab Mai 2027", date(2027, 5, 1)),
    ],
)
def test_start_dates(desc, expected):
    assert first_start(desc=desc) == expected


def test_no_false_start_on_random_dates():
    assert starts(desc="Applications close on 15 December 2026. Our company was founded in 1998.") == []


def test_end_date_not_start():
    ex = extract("", "Internship until September 2027, starting in April 2027", ref=REF)
    labels = [m.label for m in ex.starts]
    assert labels == ["avril 2027"]
    assert [m.label for m in ex.ends] == ["septembre 2027"]


def test_range_from_to():
    ex = extract("", "Internship from February to July 2027", ref=REF)
    assert ex.starts[0].start == date(2027, 2, 1)
    assert ex.duration and ex.duration.implied and round(ex.duration.min_months) == 6


def test_range_fr_du_au():
    ex = extract("", "Stage du 3 mai au 31 octobre 2027", ref=REF)
    assert ex.starts[0].start == date(2027, 5, 3)
    assert ex.duration and 5.5 <= ex.duration.min_months <= 6.5


def test_title_weak_date():
    assert first_start(title="Stage M&A – Juin 2027") == date(2027, 6, 1)
    assert first_start(title="Summer Internship 2027")  == date(2027, 6, 1)


def test_season_year_inferred():
    ex = extract("Summer Analyst Intern", "", ref=REF)
    assert ex.starts and ex.starts[0].start == date(2027, 6, 1) and ex.starts[0].year_inferred


def test_quarter_half():
    assert first_start(desc="Start date: Q2 2027") == date(2027, 4, 1)
    assert first_start(desc="Start date: H2 2027") == date(2027, 7, 1)
    assert first_start(desc="Démarrage : 2e trimestre 2027") == date(2027, 4, 1)
    assert first_start(desc="Starting in the second half of 2027") == date(2027, 7, 1)


def test_year_only_needs_keyword():
    assert starts(desc="The program runs every year since 2027") == []
    s = starts(desc="Starting in 2027")
    assert s and s[0] == (date(2027, 1, 1), date(2027, 12, 31))


def test_asap():
    ex = extract("Stage Analyste", "Démarrage dès que possible", ref=REF)
    assert ex.asap and not ex.starts
    ex = extract("Intern", "Start date: ASAP", ref=REF)
    assert ex.asap


@pytest.mark.parametrize(
    "text,lo,hi",
    [
        ("Internship duration: 6 months", 6, 6),
        ("Stage de 6 mois", 6, 6),
        ("stage de 4 à 6 mois", 4, 6),
        ("a 6-month internship", 6, 6),
        ("Durée : six mois", 6, 6),
        ("internship of 4-6 months", 4, 6),
        ("Duration: 24 weeks", 5.5, 5.6),
        ("10-week summer internship", 2.2, 2.4),
        ("Stage d'un an", 12, 12),
        ("Dauer: 6 Monate Praktikum", 6, 6),
    ],
)
def test_duration(text, lo, hi):
    d = extract_duration(norm(text))
    assert d is not None, text
    assert lo - 0.05 <= d.min_months <= hi + 0.05


@pytest.mark.parametrize(
    "text",
    [
        "Minimum 2 years of experience in M&A",
        "3 ans d'expérience requis",
        "At least 6 months of experience in a bank",
        "After 6 months you may be offered a permanent contract",
        "6 months probation period for permanent hires",
    ],
)
def test_duration_false_positives(text):
    assert extract_duration(norm(text)) is None, text


def test_evaluate_pass_and_fail():
    ex = extract("Stage M&A", "Start date: June 2027. Duration: 6 months.", ref=REF)
    v = evaluate_dates(ex, WINDOW)
    assert v.status == "PASS" and v.duration_status == "PASS"

    ex = extract("Stage M&A", "Start date: January 2027, 6 months", ref=REF)
    v = evaluate_dates(ex, WINDOW)
    assert v.status == "FAIL" and "trop tôt" in " ".join(v.reasons)

    ex = extract("Stage M&A", "Start date: October 2027", ref=REF)
    assert evaluate_dates(ex, WINDOW).status == "FAIL"

    ex = extract("Stage M&A", "Start date: June 2027. Duration: 12 months", ref=REF)
    v = evaluate_dates(ex, WINDOW)
    assert v.status == "FAIL" and v.duration_status == "FAIL"

    ex = extract("Stage M&A", "Great team in Paris.", ref=REF)
    assert evaluate_dates(ex, WINDOW).status == "UNKNOWN"

    ex = extract("Stage M&A", "Start: ASAP", ref=REF)
    assert evaluate_dates(ex, WINDOW).status == "ASAP"


def test_evaluate_partial_and_broad():
    ex = extract("", "Start date: April 2027", ref=REF)   # avril 1-30, fenêtre dès le 15 avril
    v = evaluate_dates(ex, WINDOW)
    assert v.status == "PASS" and "partial" in v.flags

    ex = extract("", "Starting in 2027", ref=REF)
    assert evaluate_dates(ex, WINDOW).status == "UNKNOWN"

    ex = extract("", "Start date: Spring 2027", ref=REF)  # mars-mai : chevauche
    assert evaluate_dates(ex, WINDOW).status == "PASS"


def test_multiple_intakes_any_pass():
    ex = extract("", "Intakes: starting in January 2027 or starting in June 2027", ref=REF)
    v = evaluate_dates(ex, WINDOW)
    assert v.status == "PASS"


def test_hint_field():
    ex = extract("Intern", "", "2027-05-17", ref=REF)
    assert ex.starts and ex.starts[0].start == date(2027, 5, 17)


def test_explicit_description_overrides_title_season():
    ex = extract("Summer Analyst Intern", "Internship runs for 6 months starting in March 2027.", ref=REF)
    assert [m.label for m in ex.starts] == ["mars 2027"]
    assert evaluate_dates(ex, WINDOW).status == "FAIL"


def test_a_compter_de():
    assert first_start(desc="Stage de 6 mois à compter de mai 2027.") == date(2027, 5, 1)


def test_experience_not_duration():
    ex = extract("Internship M&A", "You will join in July for 6 months. Previous internship experience of 3 months is a plus.", ref=REF)
    assert ex.duration and round(ex.duration.min_months) == 6
