from __future__ import annotations

from pathlib import Path

import pytest

from promptharness.core.checks import run_checks
from promptharness.core.db import Database
from promptharness.core.portable import import_harness, parse_harness

QUICKSTART = Path(__file__).resolve().parent.parent / "examples" / "quickstart.harness.yaml"

GOOD = {
    "capital-of-france": "Paris",
    "extract-person-json": '{"name": "Ada Lovelace", "age": 36}',
    "three-colors": "red, green, blue",
}
BAD = {
    "capital-of-france": "The capital of France is London.",
    "extract-person-json": "Ada Lovelace is 36.",
    "three-colors": "red, green",
}


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    d.migrate()
    return d


@pytest.fixture
def quickstart():
    harness, outputs = parse_harness(QUICKSTART.read_text(encoding="utf-8"))
    return harness, outputs


def test_quickstart_is_model_agnostic_and_self_contained(quickstart):
    harness, outputs = quickstart
    assert [c.name for c in harness.cases] == list(GOOD)
    assert harness.accepted_model is None and outputs == {}
    assert all(not c.documents for c in harness.cases)


def test_quickstart_budget_fits_reasoning_models(quickstart):
    harness, _ = quickstart
    assert harness.prompt.max_tokens is not None and harness.prompt.max_tokens >= 1024


@pytest.mark.parametrize("name", list(GOOD))
def test_quickstart_checks_accept_good_and_reject_bad_answers(quickstart, name):
    harness, _ = quickstart
    exp = next(c for c in harness.cases if c.name == name).expectation
    good = run_checks(GOOD[name], exp)
    assert good and all(r.passed for r in good), good
    bad = run_checks(BAD[name], exp)
    assert any(not r.passed for r in bad), bad


def test_quickstart_imports(db):
    h = import_harness(db, QUICKSTART.read_text(encoding="utf-8"))
    assert h.name == "quickstart"
    assert db.get_harness("quickstart") is not None
