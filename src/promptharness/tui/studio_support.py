"""Pure helpers for the Studio pane: prompt edit history and result formatting.

No Textual imports (rich only), so these are unit-testable in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from rich.text import Text

from promptharness.core.models import CaseResult, ModelRef, PromptVersion

STATUS_STYLE = {"pass": "bold green", "fail": "bold red", "error": "bold red",
                "manual": "bold yellow", "judge_error": "bold magenta"}


class PromptHistory:
    """In-session linear history of prompt versions with undo/redo."""

    def __init__(self) -> None:
        self._items: list[PromptVersion] = []
        self._pos = -1

    def __len__(self) -> int:
        return len(self._items)

    @property
    def current(self) -> PromptVersion | None:
        return self._items[self._pos] if self._pos >= 0 else None

    def push(self, version: PromptVersion) -> None:
        cur = self.current
        if cur is not None and cur.hash == version.hash:
            return
        del self._items[self._pos + 1:]
        self._items.append(version.model_copy(deep=True))
        self._pos = len(self._items) - 1

    def undo(self) -> PromptVersion | None:
        if self._pos <= 0:
            return None
        self._pos -= 1
        return self._items[self._pos]

    def redo(self) -> PromptVersion | None:
        if self._pos >= len(self._items) - 1:
            return None
        self._pos += 1
        return self._items[self._pos]


@dataclass
class _Entry:
    result: CaseResult
    prompt_hash: str
    model: ModelRef
    judge: ModelRef | None
    started_at: str
    finished_at: str
    judge_error: bool


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def format_result(r: CaseResult, model: ModelRef) -> Text:
    t = Text()
    t.append(f"── {r.case_name} ", style="bold")
    t.append(f"[{r.status.upper()}]", style=STATUS_STYLE.get(r.status, "bold"))
    meta = [str(model)]
    if r.latency_ms is not None:
        meta.append(f"{r.latency_ms} ms")
    if r.prompt_tokens is not None or r.completion_tokens is not None:
        meta.append(f"tokens {r.prompt_tokens if r.prompt_tokens is not None else '?'} in"
                    f" / {r.completion_tokens if r.completion_tokens is not None else '?'} out")
    t.append("  " + "  ".join(meta), style="dim")
    if r.output:
        t.append("\n" + r.output)
    for c in r.checks:
        mark = "✓" if c.passed else "✗"
        t.append(f"\n  {mark} {c.name}" + (f": {c.reason}" if c.reason else ""),
                 style="green" if c.passed else "red")
    for w in r.warnings:
        t.append(f"\n  ! warning: {w}", style="yellow")
    if r.error:
        t.append(f"\n  error: {r.error}", style="red")
    if r.manual_verdict is not None:
        t.append(f"\n  manual verdict: {'pass' if r.manual_verdict else 'fail'}", style="cyan")
    return t
