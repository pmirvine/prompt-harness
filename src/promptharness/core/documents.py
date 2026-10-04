from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

MAX_IMAGE_BYTES = 20 * 1024 * 1024

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
UNSUPPORTED_IMAGE_EXTENSIONS = {".bmp", ".tif", ".tiff", ".heic", ".heif", ".svg", ".ico"}


class DocumentError(Exception):
    pass


class ReadError(Exception):
    """Raised by readers with only the reason text."""


@dataclass(frozen=True)
class Document:
    name: str
    text: str
    kind: Literal["text", "image"] = "text"
    mime: str = "text/plain"
    index: int = 0
    pages: int | None = None
    warnings: tuple[str, ...] = ()
    # Underscore-prefixed so the Jinja sandbox cannot read it.
    _data: bytes | None = None


@dataclass(frozen=True)
class ReadResult:
    text: str
    mime: str = "text/plain"
    pages: int | None = None
    warnings: tuple[str, ...] = ()


Reader = Callable[[bytes, str], ReadResult]
READERS: dict[str, Reader] = {}


def register(*extensions: str) -> Callable[[Reader], Reader]:
    def deco(fn: Reader) -> Reader:
        for ext in extensions:
            READERS[ext.lower()] = fn
        return fn

    return deco


def sniff_image(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _load_optional_readers() -> None:
    for mod in ("promptharness.core.readers_office", "promptharness.core.readers_other"):
        try:
            importlib.import_module(mod)
        except ImportError as e:
            if e.name != mod:
                raise


def read_document(path: str, index: int = 0) -> Document:
    _load_optional_readers()
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise DocumentError(f"unsupported: {path}: cannot read ({e.strerror or e})") from e
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()

    if ext in IMAGE_EXTENSIONS:
        mime = sniff_image(data)
        if mime is None:
            raise DocumentError(f"unsupported: {path}: not a valid image")
        if len(data) > MAX_IMAGE_BYTES:
            raise DocumentError(
                f"unsupported: {path}: image too large ({len(data)} bytes, "
                f"limit {MAX_IMAGE_BYTES // (1024 * 1024)} MB)"
            )
        return Document(
            name=name,
            text=f"[attached image: {name}]",
            kind="image",
            mime=mime,
            index=index,
            _data=data,
        )
    if ext in UNSUPPORTED_IMAGE_EXTENSIONS:
        raise DocumentError(f"unsupported: {path}: unsupported image type ({ext})")

    reader = READERS.get(ext)
    if reader is not None:
        try:
            r = reader(data, name)
        except ReadError as e:
            raise DocumentError(f"unsupported: {path}: {e}") from e
        except Exception as e:
            raise DocumentError(
                f"unsupported: {path}: cannot read {ext} file ({type(e).__name__}: {e})"
            ) from e
        return Document(
            name=name,
            text=r.text,
            mime=r.mime,
            index=index,
            pages=r.pages,
            warnings=tuple(r.warnings),
        )

    if b"\x00" in data:
        raise DocumentError(f"unsupported: {path}: binary file")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise DocumentError(f"unsupported: {path}: not valid UTF-8 text") from e
    return Document(name=name, text=text, index=index)


def load_documents(paths: list[str]) -> list[Document]:
    return [read_document(p, i) for i, p in enumerate(paths)]
