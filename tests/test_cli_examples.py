from __future__ import annotations

from typer.testing import CliRunner

from promptharness import cli as cli_mod
from promptharness.core.db import Database
from promptharness.paths import db_path

runner = CliRunner()


def _harness_names():
    db = Database(db_path())
    try:
        return [h.name for h in db.list_harnesses()]
    finally:
        db.close()


def test_import_a_bundled_example_by_name():
    r = runner.invoke(cli_mod.app, ["import", "--example", "quickstart"])
    assert r.exit_code == 0, r.output
    assert "Imported quickstart" in r.output
    assert _harness_names() == ["quickstart"]


def test_import_unknown_example_is_a_usage_error_listing_the_choices():
    r = runner.invoke(cli_mod.app, ["import", "--example", "nope"])
    assert r.exit_code == 2
    assert "nope" in r.output and "quickstart" in r.output and "summarize" in r.output
    assert _harness_names() == []


def test_import_needs_exactly_one_source(tmp_path):
    neither = runner.invoke(cli_mod.app, ["import"])
    assert neither.exit_code == 2
    assert "--example" in neither.output
    f = tmp_path / "h.yaml"
    f.write_text("x")
    both = runner.invoke(cli_mod.app, ["import", str(f), "--example", "quickstart"])
    assert both.exit_code == 2
    assert _harness_names() == []


def test_importing_an_example_twice_needs_overwrite():
    assert runner.invoke(cli_mod.app, ["import", "--example", "quickstart"]).exit_code == 0
    again = runner.invoke(cli_mod.app, ["import", "--example", "quickstart"])
    assert again.exit_code == 1
    assert "exists" in again.output
    ok = runner.invoke(cli_mod.app, ["import", "--example", "quickstart", "--overwrite"])
    assert ok.exit_code == 0


def test_import_from_a_file_path_still_works(tmp_path):
    from promptharness.core.examples import read_example

    f = tmp_path / "mine.yaml"
    f.write_text(read_example("summarize"), encoding="utf-8")
    r = runner.invoke(cli_mod.app, ["import", str(f)])
    assert r.exit_code == 0, r.output
    assert _harness_names() == ["summarize-example"]


def test_examples_command_lists_names_and_descriptions():
    r = runner.invoke(cli_mod.app, ["examples"])
    assert r.exit_code == 0, r.output
    assert "quickstart" in r.output and "summarize" in r.output
    assert "promptharness import --example" in r.output
