from __future__ import annotations

import os
import stat

import pytest
from typer.testing import CliRunner

from promptharness import cli as cli_mod
from promptharness.envfile import load_env


@pytest.fixture
def clean_env(monkeypatch):
    """Make sure the names these tests use start unset and are restored afterwards."""
    names = ["PH_TEST_KEY", "PH_TEST_OTHER", "PH_TEST_EMPTY", "PH_TEST_HOME_ONLY", "PH_TEST_BOTH"]
    for n in names:
        monkeypatch.setenv(n, "x")
        monkeypatch.delenv(n)
    return names


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_variables_from_dotenv_in_cwd(tmp_path, clean_env):
    _write(tmp_path / ".env", "PH_TEST_KEY=abc123\nPH_TEST_OTHER=two\n")
    loaded = load_env(cwd=tmp_path, home=tmp_path / "none")
    assert os.environ["PH_TEST_KEY"] == "abc123"
    assert os.environ["PH_TEST_OTHER"] == "two"
    assert sorted(loaded) == ["PH_TEST_KEY", "PH_TEST_OTHER"]


def test_existing_environment_variables_win(tmp_path, clean_env, monkeypatch):
    monkeypatch.setenv("PH_TEST_KEY", "from-shell")
    _write(tmp_path / ".env", "PH_TEST_KEY=from-file\n")
    loaded = load_env(cwd=tmp_path, home=tmp_path / "none")
    assert os.environ["PH_TEST_KEY"] == "from-shell"
    assert loaded == []


def test_empty_values_are_treated_as_unset(tmp_path, clean_env):
    _write(tmp_path / ".env", "PH_TEST_EMPTY=\nPH_TEST_KEY=''\n")
    load_env(cwd=tmp_path, home=tmp_path / "none")
    assert "PH_TEST_EMPTY" not in os.environ
    assert "PH_TEST_KEY" not in os.environ


def test_supports_comments_quotes_and_export_prefix(tmp_path, clean_env):
    _write(
        tmp_path / ".env",
        "# a comment\n\nexport PH_TEST_KEY=\"quoted value\"  # trailing\nPH_TEST_OTHER='single'\n",
    )
    load_env(cwd=tmp_path, home=tmp_path / "none")
    assert os.environ["PH_TEST_KEY"] == "quoted value"
    assert os.environ["PH_TEST_OTHER"] == "single"


def test_data_dir_dotenv_is_a_fallback_and_cwd_wins(tmp_path, clean_env):
    cwd = tmp_path / "project"
    home = tmp_path / "home"
    cwd.mkdir()
    home.mkdir()
    _write(cwd / ".env", "PH_TEST_BOTH=cwd\n")
    _write(home / ".env", "PH_TEST_BOTH=home\nPH_TEST_HOME_ONLY=h\n")
    load_env(cwd=cwd, home=home)
    assert os.environ["PH_TEST_BOTH"] == "cwd"
    assert os.environ["PH_TEST_HOME_ONLY"] == "h"


def test_no_dotenv_files_is_not_an_error(tmp_path, clean_env):
    assert load_env(cwd=tmp_path, home=tmp_path / "missing") == []


def test_parent_directories_are_not_searched(tmp_path, clean_env):
    _write(tmp_path / ".env", "PH_TEST_KEY=parent\n")
    child = tmp_path / "child"
    child.mkdir()
    assert load_env(cwd=child, home=tmp_path / "none") == []
    assert "PH_TEST_KEY" not in os.environ


def test_unreadable_dotenv_warns_but_does_not_crash(tmp_path, clean_env, capsys):
    p = _write(tmp_path / ".env", "PH_TEST_KEY=abc\n")
    p.chmod(0)
    try:
        if os.access(p, os.R_OK):
            pytest.skip("running with privileges that ignore file permissions")
        assert load_env(cwd=tmp_path, home=tmp_path / "none") == []
    finally:
        p.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert "PH_TEST_KEY" not in os.environ
    err = capsys.readouterr().err
    assert ".env" in err and "PH_TEST_KEY" not in err


def test_values_are_never_printed(tmp_path, clean_env, capsys):
    _write(tmp_path / ".env", "PH_TEST_KEY=super-secret-value\n")
    load_env(cwd=tmp_path, home=tmp_path / "none")
    out = capsys.readouterr()
    assert "super-secret-value" not in out.out + out.err


def test_cli_loads_dotenv_before_running_a_subcommand(tmp_path, clean_env, monkeypatch):
    monkeypatch.setattr(cli_mod, "load_env", load_env)  # undo the conftest no-op
    monkeypatch.chdir(tmp_path)
    _write(tmp_path / ".env", "PH_TEST_KEY=from-cli\n")
    result = CliRunner().invoke(cli_mod.app, ["provider", "list"])
    assert result.exit_code == 0, result.output
    assert os.environ["PH_TEST_KEY"] == "from-cli"


def test_provider_add_still_works_with_a_dotenv_present(tmp_path, clean_env, monkeypatch):
    monkeypatch.setattr(cli_mod, "load_env", load_env)
    monkeypatch.chdir(tmp_path)
    _write(tmp_path / ".env", "PH_TEST_KEY=abc\n")
    runner = CliRunner()
    r = runner.invoke(
        cli_mod.app,
        ["provider", "add", "p1", "--base-url", "http://x.test/v1", "--api-key-env", "PH_TEST_KEY"],
    )
    assert r.exit_code == 0, r.output
    assert "Added" in r.output
    listed = runner.invoke(cli_mod.app, ["provider", "list"])
    assert "p1" in listed.output and "PH_TEST_KEY" in listed.output
    assert "abc" not in listed.output
