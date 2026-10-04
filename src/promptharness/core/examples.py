"""Starter harnesses bundled inside the package.

They live in `promptharness/examples/*.harness.yaml` so a normal (non-editable)
install has them. An example is referred to by its file name without the
`.harness.yaml` suffix, for example `quickstart`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable

import yaml

SUFFIX = ".harness.yaml"
PREFIX = "example:"  # how the TUI import box refers to one, for example "example:quickstart"
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class ExampleError(Exception):
    pass


@dataclass(frozen=True)
class ExampleInfo:
    name: str
    description: str


def _root() -> Traversable:
    return files("promptharness") / "examples"


def _names() -> list[str]:
    return sorted(
        e.name.removesuffix(SUFFIX)
        for e in _root().iterdir()
        if e.is_file() and e.name.endswith(SUFFIX)
    )


def _unknown(name: str) -> ExampleError:
    return ExampleError(f"unknown example {name!r}; available: {', '.join(_names())}")


def read_example(name: str) -> str:
    """Return the YAML text of a bundled example harness."""
    if not _NAME.match(name):  # also blocks path separators and ".."
        raise _unknown(name)
    entry = _root() / f"{name}{SUFFIX}"
    if not entry.is_file():
        raise _unknown(name)
    return entry.read_text(encoding="utf-8")


def list_examples() -> list[ExampleInfo]:
    """Bundled examples with the description from each harness file."""
    infos = []
    for name in _names():
        data = yaml.safe_load(read_example(name)) or {}
        description = " ".join(str(data.get("description", "")).split())
        infos.append(ExampleInfo(name=name, description=description))
    return infos
