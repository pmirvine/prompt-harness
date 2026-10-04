from __future__ import annotations

import base64
import binascii
import json
import mimetypes
import os
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
from promptharness.paths import home_dir

FORMAT_VERSION = 1
MAX_INLINE_BYTES = 10 * 1024 * 1024


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
    if body["prompt"].get("documents_name") == "documents":
        del body["prompt"]["documents_name"]
    data.update(body)
    if inline_documents:
        # Bare, unique file names: an inline export must not leak local paths.
        names: dict[str, str] = {}
        used: set[str] = set()
        for case in h.cases:
            for path in case.documents:
                if path not in names:
                    names[path] = _unique_name(_safe_component(path), used)
        for case_dict, case in zip(data["cases"], h.cases, strict=True):
            texts = []
            files = []
            for path in case.documents:
                try:
                    with open(path, "rb") as f:
                        raw = f.read(MAX_INLINE_BYTES + 1)
                except OSError as e:
                    raise PortableError(f"cannot read document {path}: {e}") from e
                if len(raw) > MAX_INLINE_BYTES:
                    raise PortableError(
                        f"document {path} is larger than 10 MB and cannot be inlined"
                    )
                text = _inline_text(raw)
                if text is not None:
                    texts.append({"name": names[path], "text": text})
                else:
                    files.append(
                        {
                            "name": names[path],
                            "mime": mimetypes.guess_type(path)[0] or "application/octet-stream",
                            "base64": base64.b64encode(raw).decode("ascii"),
                        }
                    )
            case_dict["documents"] = [names[p] for p in case.documents]
            case_dict["document_texts"] = texts
            if files:
                case_dict["document_files"] = files
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
    harness, outputs, _, _ = _parse(text, fmt)
    return harness, outputs


def _parse(
    text: str, fmt: str | None = None
) -> tuple[
    Harness, dict[str, str], dict[str, dict[str, str]], dict[str, dict[str, bytes]]
]:
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
    doc_texts: dict[str, dict[str, str]] = {}
    doc_files: dict[str, dict[str, bytes]] = {}
    for c in data.get("cases") or []:
        if not isinstance(c, dict):
            continue
        entries = c.pop("document_texts", None) or []
        try:
            doc_texts[str(c.get("name"))] = {str(e["name"]): str(e["text"]) for e in entries}
        except (TypeError, KeyError) as e:
            raise PortableError(f"invalid document_texts in case {c.get('name')!r}") from e
        file_entries = c.pop("document_files", None) or []
        try:
            if not isinstance(file_entries, list):
                raise TypeError("document_files must be a list")
            decoded: dict[str, bytes] = {}
            for e in file_entries:
                if not isinstance(e, dict) or not isinstance(e["base64"], str):
                    raise TypeError("bad entry")
                decoded[str(e["name"])] = base64.b64decode(e["base64"], validate=True)
            doc_files[str(c.get("name"))] = decoded
        except (TypeError, KeyError, ValueError, binascii.Error) as e:
            raise PortableError(f"invalid document_files in case {c.get('name')!r}") from e
    try:
        if data.get("accepted_model") is not None:
            data["accepted_model"] = ModelRef.parse(str(data["accepted_model"]))
        harness = Harness.model_validate(data)
    except (ValidationError, ValueError) as e:
        raise PortableError(f"invalid harness: {e}") from e
    outputs = {str(k): str(v) for k, v in outputs.items()}
    if outputs:
        if harness.accepted_model is None:
            raise PortableError("accepted_outputs present but accepted_model is missing")
        unknown = sorted(set(outputs) - {c.name for c in harness.cases})
        if unknown:
            raise PortableError(f"accepted_outputs name unknown cases: {', '.join(unknown)}")
    return harness, outputs, doc_texts, doc_files


def _safe_component(name: str) -> str:
    base = os.path.basename(name.replace("\\", "/"))
    if base in ("", ".", ".."):
        base = "document"
    return base


def _unique_name(fname: str, used: set[str]) -> str:
    """Return `fname`, or `2_fname`, `3_fname`, ... - the first one not in `used` (added)."""
    candidate, n = fname, 2
    while candidate in used:
        candidate = f"{n}_{fname}"
        n += 1
    used.add(candidate)
    return candidate


def _inline_text(raw: bytes) -> str | None:
    """The text to inline as `document_texts`, or None to inline the bytes as a file.

    Text must be UTF-8 without NUL bytes and survive a YAML dump/load unchanged (e.g.
    U+0085 does not); the same rule is used for JSON exports so both formats agree.
    """
    if b"\x00" in raw:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if yaml.safe_load(yaml.safe_dump(text, allow_unicode=True)) != text:
        return None
    return text


def _restore_documents(
    harness: Harness,
    doc_texts: dict[str, dict[str, str]],
    doc_files: dict[str, dict[str, bytes]] | None = None,
) -> Harness:
    doc_files = doc_files or {}
    root = home_dir() / "documents" / _safe_component(harness.name)
    used: set[str] = set()
    written: dict[tuple[str, bytes], str] = {}
    cases = []
    for case in harness.cases:
        texts = doc_texts.get(case.name, {})
        files = doc_files.get(case.name, {})
        new_docs = []
        for path in case.documents:
            if path in texts:
                data = texts[path].encode("utf-8")
            elif path in files:
                data = files[path]
            else:
                new_docs.append(path)
                continue
            # Legacy exports carried absolute paths: keep them if the file is still there.
            # Bare names are never resolved against the current directory.
            if os.path.isabs(path) and os.path.exists(path):
                new_docs.append(path)
                continue
            key = (path, data)
            if key not in written:
                root.mkdir(parents=True, exist_ok=True)
                target = root / _unique_name(_safe_component(path), used)
                target.write_bytes(data)
                written[key] = str(target)
            new_docs.append(written[key])
        cases.append(case.model_copy(update={"documents": new_docs}))
    return harness.model_copy(update={"cases": cases})


def import_harness(db: Database, text: str, overwrite: bool = False) -> Harness:
    harness, outputs, doc_texts, doc_files = _parse(text)
    previous = db.get_harness(harness.name)
    if previous is not None and not overwrite:
        raise PortableError(f"harness '{harness.name}' exists")
    try:
        harness = _restore_documents(harness, doc_texts, doc_files)
        db.save_harness(harness)
        if outputs and harness.accepted_model is not None:
            now = datetime.now(timezone.utc).isoformat()
            results = [
                CaseResult(
                    case_name=c.name,
                    status=final_status([], None, False, None),
                    output=outputs[c.name],
                )
                for c in harness.cases
                if c.name in outputs
            ]
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
    except Exception as e:
        # Compensating rollback. Known limitation: Database has no delete_run, so a
        # run saved before a later failure (set_accepted) would remain as an orphan.
        try:
            if previous is not None:
                db.save_harness(previous)
            else:
                db.delete_harness(harness.name)
        except Exception:
            pass
        raise PortableError(f"import failed: {e}") from e
    if saved is None:
        raise PortableError(f"import failed: harness '{harness.name}' not saved")
    return saved
