"""Compare screen: one case's outputs side by side across models, accepted output first."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, Static

from promptharness.core.models import CaseResult
from promptharness.tui.studio_support import STATUS_STYLE

__all__ = ["CompareScreen", "ComparePane"]

Column = tuple[str, "CaseResult | None"]


def _header(label: str, r: CaseResult | None) -> Text:
    t = Text(label, style="bold #7fd1b9")
    if r is None:
        return t
    t.append("\n")
    t.append(r.status.upper(), style=STATUS_STYLE.get(r.status, "bold"))
    t.append(f"  {r.latency_ms} ms" if r.latency_ms is not None else "  - ms", style="dim")
    if r.prompt_tokens is not None or r.completion_tokens is not None:
        pin = r.prompt_tokens if r.prompt_tokens is not None else "?"
        pout = r.completion_tokens if r.completion_tokens is not None else "?"
        t.append(f"  tokens {pin} in / {pout} out", style="dim")
    else:
        t.append("  tokens -", style="dim")
    return t


def _body(r: CaseResult | None) -> Text:
    if r is None:
        return Text("no result", style="dim italic")
    t = Text(r.output or "")
    if not r.output:
        t.append("(empty output)", style="dim")
    if r.error:
        t.append(f"\n\nerror: {r.error}", style="red")
    for c in r.checks:
        if not c.passed:
            t.append(f"\n✗ {c.name}" + (f": {c.reason}" if c.reason else ""), style="red")
    for w in r.warnings:
        t.append(f"\n! {w}", style="yellow")
    if r.manual_verdict is not None:
        t.append(f"\nmanual verdict: {'pass' if r.manual_verdict else 'fail'}", style="cyan")
    return t


class ComparePane(VerticalScroll, can_focus=True):
    """One column: header (label, status, latency, tokens) over the wrapped output."""

    def __init__(self, label: str, result: CaseResult | None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.label = label
        self.result = result
        self.header_text = _header(label, result)
        self.body_text = _body(result)

    def compose(self) -> ComposeResult:
        yield Static(self.header_text, classes="compare-head")
        yield Static(self.body_text, classes="compare-body")

    @property
    def text(self) -> str:
        return f"{self.header_text.plain}\n{self.body_text.plain}"


class CompareScreen(Screen):
    # Priority: the focused pane's own left/right (horizontal scroll) must not win.
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("left", "move(-1)", "Prev pane", priority=True),
        Binding("right", "move(1)", "Next pane", priority=True),
    ]

    def __init__(self, case_name: str, columns: list[Column]) -> None:
        super().__init__()
        self.case_name = case_name
        self.columns = list(columns)

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label(f"Compare: {self.case_name}  ·  ←/→ = pane, esc = back",
                    id="compare-title")
        with Horizontal(id="compare-panes"):
            for i, (label, r) in enumerate(self.columns):
                yield ComparePane(label, r, id=f"compare-pane-{i}", classes="compare-pane")
        yield Footer()

    def on_mount(self) -> None:
        if self.panes:
            self.panes[0].focus()

    @property
    def panes(self) -> list[ComparePane]:
        return list(self.query(ComparePane))

    @property
    def labels(self) -> list[str]:
        return [label for label, _ in self.columns]

    @property
    def pane_texts(self) -> list[str]:
        return [p.text for p in self.panes]

    def action_move(self, step: int) -> None:
        panes = self.panes
        if not panes:
            return
        try:
            i = panes.index(self.focused)  # type: ignore[arg-type]
        except ValueError:
            i = -step if step > 0 else 0
        panes[max(0, min(len(panes) - 1, i + step))].focus()

    def action_back(self) -> None:
        self.dismiss()
