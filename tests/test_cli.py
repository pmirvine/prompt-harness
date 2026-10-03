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


def _two_case_harness():
    return Harness(
        name="h2",
        prompt=PromptVersion(template="{{input}}"),
        cases=[
            Case(name="c1", input="x", expectation=Expectation(must_include=[Match(pattern="ok")])),
            Case(name="c2", input="y", expectation=Expectation(must_include=[Match(pattern="ok")])),
        ],
    )


def test_run_missing_env_var_reason_shown(setup, monkeypatch):
    from promptharness.core.client import OpenAIChatClient

    setup([])
    monkeypatch.delenv("K", raising=False)
    monkeypatch.setattr(cli, "client_factory", OpenAIChatClient)
    r = runner.invoke(cli.app, ["run", "h", "--model", "p:m"])
    assert r.exit_code == 1
    assert "c1 × p:m: error — config: environment variable K is not set" in r.output


def test_run_failed_check_reason_shown(setup):
    setup(["nope"])
    r = runner.invoke(cli.app, ["run", "h", "--model", "p:m"])
    assert r.exit_code == 1
    assert "c1 × p:m: fail — include:ok: pattern not found: 'ok'" in r.output


def test_run_warning_shown(setup):
    from promptharness.core.client import ChatResult

    setup([ChatResult("ok", None, None, 0, {}, {}, warnings=["dropped param: temperature"])])
    r = runner.invoke(cli.app, ["run", "h", "--model", "p:m"])
    assert r.exit_code == 0, r.output
    assert "c1 × p:m: warning: dropped param: temperature" in r.output


def test_run_judge_used_and_reason_shown(monkeypatch):
    db = _db()
    db.save_provider(Provider(name="p", base_url="http://x", api_key_env="K"))
    db.save_harness(
        Harness(
            name="hj",
            prompt=PromptVersion(template="{{input}}"),
            cases=[Case(name="c1", input="x", expectation=Expectation(judge_prompt="be nice"))],
        )
    )
    db.close()
    fake = FakeClient(["answer", '{"pass": false, "reason": "too terse"}'])
    monkeypatch.setattr(cli, "client_factory", lambda: fake)
    r = runner.invoke(cli.app, ["run", "hj", "--model", "p:m", "--judge", "p:judge-model"])
    assert r.exit_code == 1, r.output
    assert len(fake.calls) == 2
    assert fake.calls[1]["model"] == "judge-model"
    assert "c1 × p:m: fail — judge: too terse" in r.output


def test_run_only_selected_case(monkeypatch):
    db = _db()
    db.save_provider(Provider(name="p", base_url="http://x", api_key_env="K"))
    db.save_harness(_two_case_harness())
    db.close()
    fake = FakeClient(["ok"])
    monkeypatch.setattr(cli, "client_factory", lambda: fake)
    r = runner.invoke(cli.app, ["run", "h2", "--model", "p:m", "--case", "c2"])
    assert r.exit_code == 0, r.output
    assert len(fake.calls) == 1
    assert fake.calls[0]["messages"][-1]["content"] == "y"
    assert "c1" not in r.output
    assert "1 pass, 0 fail, 0 error, 0 judge_error, 0 manual" in r.output


def test_run_summary_line_counts(monkeypatch):
    db = _db()
    db.save_provider(Provider(name="p", base_url="http://x", api_key_env="K"))
    db.save_harness(_two_case_harness())
    db.close()

    def by_input(provider, model, messages, params):
        return "ok" if messages[-1]["content"] == "x" else "nope"

    fake = FakeClient([by_input] * 4)
    monkeypatch.setattr(cli, "client_factory", lambda: fake)
    r = runner.invoke(cli.app, ["run", "h2", "--model", "p:a", "--model", "p:b"])
    assert r.exit_code == 1, r.output
    lines = r.output.strip().splitlines()
    assert lines[-1] == "2 pass, 2 fail, 0 error, 0 judge_error, 0 manual"
    assert "c2 × p:a: fail — " in r.output
    assert "c2 × p:b: fail — " in r.output


def test_provider_add_max_tokens_param():
    r = runner.invoke(
        cli.app,
        ["provider", "add", "acme", "--base-url", "http://a/v1", "--api-key-env", "K",
         "--max-tokens-param", "max_completion_tokens"],
    )
    assert r.exit_code == 0, r.output
    assert _db().get_provider("acme").max_tokens_param == "max_completion_tokens"


def test_provider_add_max_tokens_param_default_and_invalid():
    r = runner.invoke(
        cli.app, ["provider", "add", "acme", "--base-url", "http://a/v1", "--api-key-env", "K"]
    )
    assert r.exit_code == 0, r.output
    assert _db().get_provider("acme").max_tokens_param == "max_tokens"
    r = runner.invoke(
        cli.app,
        ["provider", "add", "bad", "--base-url", "http://a/v1", "--api-key-env", "K",
         "--max-tokens-param", "tokens"],
    )
    assert r.exit_code == 2
    assert _db().get_provider("bad") is None


def test_provider_add_without_api_key_env():
    r = runner.invoke(
        cli.app, ["provider", "add", "local", "--base-url", "http://localhost:8000/v1"]
    )
    assert r.exit_code == 0, r.output
    assert _db().get_provider("local").api_key_env == ""
