"""Build chat message content from rendered text and documents, and make
requests safe to store (no base64 image payloads)."""

from __future__ import annotations

import base64
import re
from collections.abc import Sequence
from typing import Any

from promptharness.core.documents import Document

_DATA_URL = re.compile(r"^data:([^;,]*);base64,(.*)$", re.DOTALL)
_OMITTED = re.compile(r"^<\d+ bytes omitted>$")


def _data_url(doc: Document) -> str:
    b64 = base64.b64encode(doc._data or b"").decode("ascii")
    return f"data:{doc.mime};base64,{b64}"


def build_user_content(text: str, documents: Sequence[Document]) -> str | list[dict]:
    """The plain text when no image documents; otherwise a text part followed by
    one image_url part per image document, in document order."""
    images = [d for d in documents if d.kind == "image"]
    if not images:
        return text
    parts: list[dict] = [{"type": "text", "text": text}]
    parts.extend({"type": "image_url", "image_url": {"url": _data_url(d)}} for d in images)
    return parts


def _decoded_size(payload: str) -> int:
    p = "".join(payload.split()).rstrip("=")
    return len(p) * 3 // 4


def _redact_url(url: Any) -> Any:
    if not isinstance(url, str):
        return url
    m = _DATA_URL.match(url)
    if m is None or _OMITTED.match(m.group(2)):
        return url
    return f"data:{m.group(1)};base64,<{_decoded_size(m.group(2))} bytes omitted>"


def _redact_part(part: Any) -> Any:
    if not isinstance(part, dict) or "image_url" not in part:
        return part
    img = part["image_url"]
    if isinstance(img, dict):
        img = {**img, "url": _redact_url(img.get("url"))}
    else:
        img = _redact_url(img)
    return {**part, "image_url": img}


def redact_images(messages: list[dict]) -> list[dict]:
    """A copy of messages with every base64 data URL replaced by a size placeholder.
    Never mutates its input; redacting twice changes nothing."""
    out: list[dict] = []
    for msg in messages:
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list):
            msg = {**msg, "content": [_redact_part(p) for p in content]}
        out.append(msg)
    return out


def summarize_documents(documents: Sequence[Document]) -> list[dict]:
    out: list[dict] = []
    for d in documents:
        s: dict[str, Any] = {"name": d.name, "kind": d.kind, "mime": d.mime}
        if d.kind == "image":
            s["bytes"] = len(d._data or b"")
        else:
            s["chars"] = len(d.text)
        if d.pages is not None:
            s["pages"] = d.pages
        out.append(s)
    return out
