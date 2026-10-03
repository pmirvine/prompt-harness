import pytest
from conftest import FakeClient
from typer.testing import CliRunner

from promptharness import cli, paths
from promptharness.core.db import Database
from promptharness.core.models import Case, Expectation, Harness, Match, Provider, PromptVersion

runner = CliRunner()


def _db():
    return Database(paths.db_path())


def _harness(name="h", with_check=True):
    exp = Expectation(must_include=[Match(pattern="ok")]) if with_check else Expectation()
    return Harness(
        name=name,
        prompt=PromptVersion(template="{{input}}"),
        cases=[Case(name="c1", input="x", expectation=exp)],
    )


@pytest.fixture
def setup(monkeypatch):
    def make(script, with_check=True):
        db = _db()
        db.save_provider(Provider(name="p", base_url="http://x", api_key_env="K"))
        db.save_harness(_harness(with_check=with_check))
        db.close()
        fake = FakeClient(script)
        monkeypatch.setattr(cli, "client_factory", lambda: fake)
        return fake

    return make


def test_run_all_pass_exit_zero(setup):
    setup(["ok"])
    r = runner.invoke(cli.app, ["run", "h", "--model", "p:m"])
    assert r.exit_code == 0, r.output
    assert "c1" in r.output and "pass" in r.output


def test_run_with_failure_exit_one(setup):
    setup(["nope"])
    r = runner.invoke(cli.app, ["run", "h", "--model", "p:m"])
    assert r.exit_code == 1
    assert "fail" in r.output


def test_run_manual_only_exit_zero(setup):
    setup(["anything"], with_check=False)
    r = runner.invoke(cli.app, ["run", "h", "--model", "p:m"])
    assert r.exit_code == 0
    assert "manual" in r.output


def test_run_unknown_harness_exit_two_with_message(setup):
    setup([])
    r = runner.invoke(cli.app, ["run", "nope", "--model", "p:m"])
    assert r.exit_code == 2
    assert "nope" in r.output


def test_run_bad_model_spec_exit_two(setup):
    setup([])
    r = runner.invoke(cli.app, ["run", "h", "--model", "badspec"])
    assert r.exit_code == 2
    assert "badspec" in r.output


def test_run_unknown_case_exit_two(setup):
    setup([])
    r = runner.invoke(cli.app, ["run", "h", "--model", "p:m", "--case", "zzz"])
    assert r.exit_code == 2
    assert "zzz" in r.output


def test_run_persists_runs(setup):
    setup(["ok", "ok"])
    r = runner.invoke(cli.app, ["run", "h", "--model", "p:a", "--model", "p:b"])
    assert r.exit_code == 0, r.output
    db = _db()
    assert len(db.list_runs()) == 2


def test_export_then_import_roundtrip(setup, tmp_path):
    setup([])
    out = tmp_path / "h.yaml"
    r = runner.invoke(cli.app, ["export", "h", "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert out.exists()
    db = _db()
    db.delete_harness("h")
    db.close()
    r = runner.invoke(cli.app, ["import", str(out)])
    assert r.exit_code == 0, r.output
    assert _db().get_harness("h") is not None


def test_import_existing_without_overwrite_exit_one(setup, tmp_path):
    setup([])
    out = tmp_path / "h.json"
    assert runner.invoke(cli.app, ["export", "h", "--format", "json", "--out", str(out)]).exit_code == 0
    r = runner.invoke(cli.app, ["import", str(out)])
    assert r.exit_code == 1
    r = runner.invoke(cli.app, ["import", str(out), "--overwrite"])
    assert r.exit_code == 0, r.output


def test_provider_add_and_list():
    r = runner.invoke(
        cli.app,
        ["provider", "add", "acme", "--base-url", "http://a/v1", "--api-key-env", "ACME_KEY"],
    )
    assert r.exit_code == 0, r.output
    r = runner.invoke(cli.app, ["provider", "list"])
    assert "acme" in r.output and "http://a/v1" in r.output and "ACME_KEY" in r.output
