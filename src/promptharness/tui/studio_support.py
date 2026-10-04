"""Pure helpers for the Studio pane: prompt edit history and result formatting.

No Textual imports (rich + core only), so these are unit-testable in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from rich.text import Text

from promptharness.core.db import Database
from promptharness.core.models import Case, CaseResult, ModelRef, PromptVersion, Run
from promptharness.core.status import final_status

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
class StudioEntry:
    """A studio result plus the exact context (case, prompt, models) that produced it."""

    case: Case
    result: CaseResult
    prompt_hash: str
    model: ModelRef
    judge: ModelRef | None
    started_at: str
    finished_at: str
    judge_error: bool


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def documents_line(summaries: object) -> str:
    """`documents: a.txt (text) · b.pdf (text, 3 pages) · c.png (image)`, or ""."""
    if not isinstance(summaries, list) or not summaries:
        return ""
    parts = []
    for d in summaries:
        if not isinstance(d, dict):
            continue
        detail = str(d.get("kind", "?"))
        pages = d.get("pages")
        if isinstance(pages, int):
            detail += f", {pages} page" + ("" if pages == 1 else "s")
        parts.append(f"{d.get('name', '?')} ({detail})")
    return "documents: " + " · ".join(parts) if parts else ""


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
    docs = documents_line(r.request.get("documents"))
    if docs:
        t.append("\n" + docs, style="dim")
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


def save_accepted_run(
    db: Database,
    harness: str,
    prompt_hash: str,
    model: ModelRef,
    judge: ModelRef | None,
    entries: list[StudioEntry],
) -> int:
    """Store entries' results as a run and mark it accepted for `harness`; returns run id.

    db.save_run derives the judge_error flag from status, which a pre-save manual verdict
    has overwritten. Those results are stored as judge_error and the verdict is re-applied
    through db.set_manual_verdict, so the flag persists and status follows final_status.
    Result objects get their db ids, so later verdicts are written to the database.
    """
    run = Run(harness=harness, prompt_hash=prompt_hash, model=model, judge_model=judge,
              started_at=min(e.started_at for e in entries),
              finished_at=max(e.finished_at for e in entries),
              results=[e.result for e in entries])
    reapply: list[tuple[CaseResult, bool]] = []
    for e in entries:
        r = e.result
        if e.judge_error and r.manual_verdict is not None:
            reapply.append((r, r.manual_verdict))
            r.status, r.manual_verdict = "judge_error", None
    ok = False
    try:
        run_id = db.save_run(run)
        ok = True
    finally:
        if not ok:  # restore in-memory state
            for r, verdict in reapply:
                r.manual_verdict = verdict
                r.status = final_status(r.checks, r.error, True, verdict)
    for r, verdict in reapply:
        saved = db.set_manual_verdict(r.id, verdict)
        r.status, r.manual_verdict = saved.status, saved.manual_verdict
    db.set_accepted(harness, model, run_id)
    return run_id
