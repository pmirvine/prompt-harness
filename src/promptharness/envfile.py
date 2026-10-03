"""Load API keys from `.env` files into the process environment.

Precedence, highest first: variables already set in the real environment, then
`./.env` (current directory only, parents are not searched), then
`<data dir>/.env`. Empty values are treated as unset. Values are never printed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import dotenv_values

from promptharness import paths


def load_env(cwd: Path | None = None, home: Path | None = None) -> list[str]:
    """Set missing environment variables from `.env` files; return the names set."""
    directories = [cwd if cwd is not None else Path.cwd(), home if home is not None else paths.home_dir()]
    loaded: list[str] = []
    seen: set[Path] = set()
    for directory in directories:
        path = directory / ".env"
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        try:
            values = dotenv_values(path)
        except OSError as exc:
            print(f"promptharness: could not read {path}: {exc.strerror or exc}", file=sys.stderr)
            continue
        for name, value in values.items():
            if value and name not in os.environ:
                os.environ[name] = value
                loaded.append(name)
    return loaded
