import os
from pathlib import Path

from platformdirs import user_data_dir


def home_dir() -> Path:
    """Return the PromptHarness data directory, creating it if needed."""
    env = os.environ.get("PROMPTHARNESS_HOME")
    path = Path(env) if env else Path(user_data_dir("promptharness"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return home_dir() / "promptharness.db"
