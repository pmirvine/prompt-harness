from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

import yaml
from pydantic import ValidationError

from promptharness.core.db import Database
from promptharness.core.models import (
    CaseResult,
    Harness,
    ModelRef,
    Run,
)
from promptharness.core.status import final_status

FORMAT_VERSION = 1


class PortableError(Exception):
    pass


def export_harness(
    db: Database,
    name: str,
    fmt: Literal["yaml", "json"] = "yaml",
    inline_documents: bool = False,
) -> str:
    h = db.get_harness(name)
    if h is None:
        raise PortableError(f"harness '{name}' not found")
    data: dict[str, Any] = {"format_version": FORMAT_VERSION}
    body = h.model_dump(mode="json", exclude={"accepted_model", "accepted_run_id"})
    data.update(body)
    if inline_documents:
        for case_dict, case in zip(data["cases"], h.cases, strict=True):
            texts = []
            for path in case.documents:
                try:
                    with open(path, encoding="utf-8") as f:
                        texts.append({"name": path, "text": f.read()})
                except (OSError, UnicodeDecodeError):
                    texts.append({"name": path, "text": ""})
            case_dict["document_texts"] = texts
    if h.accepted_model is not None:
        data["accepted_model"] = str(h.accepted_model)
    if h.accepted_run_id is not None:
        run = db.get_run(h.accepted_run_id)
        if run is not None:
            data["accepted_outputs"] = {r.case_name: r.output for r in run.results}
    if fmt == "json":
        return json.dumps(data, indent=2, ensure_ascii=False)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def parse_harness(text: str, fmt: str | None = None) -> tuple[Harness, dict[str, str]]:
    if fmt is None:
        fmt = "json" if text.lstrip().startswith("{") else "yaml"
    try:
        if fmt == "json":
            data = json.loads(text)
        elif fmt == "yaml":
            data = yaml.safe_load(text)
        else:
            raise PortableError(f"unknown format: {fmt!r}")
    except (yaml.YAMLError, json.JSONDecodeError) as e:
        raise PortableError(f"malformed {fmt}: {e}") from e
    if not isinstance(data, dict):
        raise PortableError("malformed content: expected a mapping at top level")
    version = data.get("format_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise PortableError("missing or invalid format_version")
    if version > FORMAT_VERSION:
        raise PortableError(
            f"unsupported format_version {version} (this version supports {FORMAT_VERSION})"
        )
    if version < 1:
        raise PortableError(f"invalid format_version {version}")
    data = {k: v for k, v in data.items() if k != "format_version"}
    outputs = data.pop("accepted_outputs", None) or {}
    if not isinstance(outputs, dict):
        raise PortableError("accepted_outputs must be a mapping")
    data.pop("accepted_run_id", None)
    try:
        if data.get("accepted_model") is not None:
            data["accepted_model"] = ModelRef.parse(str(data["accepted_model"]))
        harness = Harness.model_validate(data)
    except (ValidationError, ValueError) as e:
        raise PortableError(f"invalid harness: {e}") from e
    return harness, {str(k): str(v) for k, v in outputs.items()}


def import_harness(db: Database, text: str, overwrite: bool = False) -> Harness:
    harness, outputs = parse_harness(text)
    if db.get_harness(harness.name) is not None and not overwrite:
        raise PortableError(f"harness '{harness.name}' exists")
    db.save_harness(harness)
    if outputs and harness.accepted_model is not None:
        now = datetime.now(timezone.utc).isoformat()
        results = []
        for c in harness.cases:
            if c.name in outputs:
                results.append(
                    CaseResult(
                        case_name=c.name,
                        status=final_status([], None, False, None),
                        output=outputs[c.name],
                    )
                )
        run = Run(
            harness=harness.name,
            prompt_hash=harness.prompt.hash,
            model=harness.accepted_model,
            started_at=now,
            finished_at=now,
            results=results,
        )
        run_id = db.save_run(run)
        db.set_accepted(harness.name, harness.accepted_model, run_id)
    saved = db.get_harness(harness.name)
    assert saved is not None
    return saved
