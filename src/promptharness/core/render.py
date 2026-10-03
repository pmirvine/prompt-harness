from __future__ import annotations

import os
from dataclasses import dataclass

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment


@dataclass(frozen=True)
class Document:
    name: str
    text: str


class DocumentError(Exception):
    pass


class TemplateRenderError(Exception):
    pass


def load_documents(paths: list[str]) -> list[Document]:
    docs: list[Document] = []
    for p in paths:
        try:
            with open(p, "rb") as f:
                data = f.read()
        except OSError as e:
            raise DocumentError(f"unsupported: {p}: cannot read ({e.strerror or e})") from e
        if b"\x00" in data:
            raise DocumentError(f"unsupported: {p}: binary file")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise DocumentError(f"unsupported: {p}: not valid UTF-8 text") from e
        docs.append(Document(name=os.path.basename(p), text=text))
    return docs


def render_user(template: str, input: str, documents: list[Document]) -> str:
    env = SandboxedEnvironment(undefined=StrictUndefined)
    try:
        return env.from_string(template).render(input=input, documents=documents)
    except TemplateError as e:
        raise TemplateRenderError(str(e)) from e
    except Exception as e:  # sandbox security errors, attribute errors in templates
        raise TemplateRenderError(f"{type(e).__name__}: {e}") from e
