import pytest


@pytest.fixture(autouse=True)
def _promptharness_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMPTHARNESS_HOME", str(tmp_path / "ph-home"))
