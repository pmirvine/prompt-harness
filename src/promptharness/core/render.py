from __future__ import annotations

from typing import Any

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from promptharness.core.documents import Document, DocumentError, load_documents

__all__ = ["Document", "DocumentError", "TemplateRenderError", "load_documents", "render_template", "render_user"]


class TemplateRenderError(Exception):
    pass


def render_template(template: str, variables: dict[str, Any]) -> str:
    """Render a Jinja2 template in a sandbox with strict undefined variables.

    Any failure (syntax, undefined name, sandbox violation, ...) raises TemplateRenderError.
    """
    env = SandboxedEnvironment(undefined=StrictUndefined)
    try:
        return env.from_string(template).render(variables)
    except TemplateError as e:
        raise TemplateRenderError(str(e)) from e
    except Exception as e:  # sandbox security errors, attribute errors in templates
        raise TemplateRenderError(f"{type(e).__name__}: {e}") from e


def render_user(
    template: str, input: str, documents: list[Document], documents_name: str = "documents"
) -> str:
    return render_template(template, {"input": input, documents_name: documents})
