from promptharness.paths import db_path, home_dir


def test_home_dir_respects_env(tmp_path, monkeypatch):
    target = tmp_path / "x"
    monkeypatch.setenv("PROMPTHARNESS_HOME", str(target))
    assert home_dir() == target
    assert target.is_dir()
    assert db_path().name == "promptharness.db"
