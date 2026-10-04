from __future__ import annotations

from pathlib import Path

import pytest

import promptharness

from promptharness.core.checks import run_checks
from promptharness.core.db import Database
from promptharness.core.examples import ExampleError, list_examples, read_example
from promptharness.core.portable import import_harness, parse_harness


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
    harness, outputs = parse_harness(read_example("quickstart"))
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
    h = import_harness(db, read_example("quickstart"))
    assert h.name == "quickstart"
    assert db.get_harness("quickstart") is not None


def test_examples_are_bundled_inside_the_package():
    # A normal (non-editable) install only contains files under src/promptharness/.
    pkg = Path(promptharness.__file__).resolve().parent / "examples"
    assert (pkg / "quickstart.harness.yaml").is_file()
    assert (pkg / "summarize.harness.yaml").is_file()


def test_list_examples_names_and_descriptions():
    infos = list_examples()
    assert [i.name for i in infos] == ["quickstart", "summarize"]
    assert all(i.description.strip() for i in infos)
    # Descriptions come from the harness files, with folded YAML whitespace collapsed.
    assert "\n" not in infos[0].description


@pytest.mark.parametrize("name", ["quickstart", "summarize"])
def test_every_bundled_example_parses_and_imports(db, name):
    harness, _ = parse_harness(read_example(name))
    assert harness.cases
    assert import_harness(db, read_example(name)).name == harness.name


def test_unknown_example_lists_the_available_ones():
    with pytest.raises(ExampleError) as exc:
        read_example("nope")
    assert "nope" in str(exc.value)
    assert "quickstart" in str(exc.value) and "summarize" in str(exc.value)


@pytest.mark.parametrize("bad", ["", "../quickstart", "a/b", "quickstart.harness.yaml", "Quickstart"])
def test_example_names_cannot_escape_the_examples_folder(bad):
    with pytest.raises(ExampleError):
        read_example(bad)
