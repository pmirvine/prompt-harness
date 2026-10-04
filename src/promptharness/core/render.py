from __future__ import annotations

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from promptharness.core.documents import Document, DocumentError, load_documents

__all__ = ["Document", "DocumentError", "TemplateRenderError", "load_documents", "render_user"]


class TemplateRenderError(Exception):
    pass


def render_user(template: str, input: str, documents: list[Document]) -> str:
    env = SandboxedEnvironment(undefined=StrictUndefined)
    try:
        return env.from_string(template).render(input=input, documents=documents)
    except TemplateError as e:
        raise TemplateRenderError(str(e)) from e
    except Exception as e:  # sandbox security errors, attribute errors in templates
        raise TemplateRenderError(f"{type(e).__name__}: {e}") from e
