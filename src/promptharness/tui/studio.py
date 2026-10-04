"""Prompt Studio: edit a prompt, run it against in-memory cases, save as a harness."""

from __future__ import annotations

from pydantic import ValidationError
from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import (
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Select,
    TabbedContent,
    TabPane,
    TextArea,
)

from promptharness.core.models import Case, CaseResult, Harness, ModelRef, PromptVersion
from promptharness.core.runner import run_harness
from promptharness.tui.studio_modals import CaseForm
from promptharness.tui.studio_persist import StudioPersistMixin
from promptharness.tui.studio_support import (
    PromptHistory,
    StudioEntry,
    format_result,
    now_iso,
)

__all__ = ["PromptHistory", "StudioPane"]

DEFAULT_TEMPLATE = "{{ input }}"


def _template_label(documents_name: str) -> str:
    return f"User template (Jinja2: input, {documents_name})"


def _valid_documents_name(raw: str) -> str:
    """The name the template will see: `raw` if valid, otherwise the default."""
    name = raw.strip()
    if not name:
        return "documents"
    try:
        PromptVersion(template="", documents_name=name)
    except ValidationError:
        return "documents"
    return name
HINT = ("Tab to cases: n new · x delete · enter edit · r run · R run all · v verdict · s save"
        "  |  anywhere: ctrl+r run · ctrl+s save · alt+←/→ prompt history"
        " (ctrl+z/y outside editors)")


class CaseList(ListView):
    """Case list; the studio's single-letter keys are scoped to it so editors never see them."""

    BINDINGS = [
        Binding("n", "studio('new_case')", "New case"),
        Binding("x", "studio('delete_case')", "Delete case"),
        Binding("r", "studio('run_selected')", "Run case"),
        Binding("R", "studio('run_all')", "Run all"),
        Binding("v", "studio('verdict')", "Verdict"),
        Binding("s", "studio('save')", "Save harness"),
    ]

    async def action_studio(self, name: str) -> None:
        pane = next(a for a in self.ancestors if isinstance(a, StudioPane))
        await getattr(pane, f"action_{name}")()


class StudioPane(StudioPersistMixin, Widget):
    HISTORY_PAUSE = 2.0
    BINDINGS = [
        Binding("ctrl+z", "history_undo", "Prompt back"),
        Binding("ctrl+y", "history_redo", "Prompt fwd"),
        # Not bound by TextArea/Input, so these also work while an editor has focus.
        Binding("alt+left", "history_undo", "Prompt back", show=False),
        Binding("alt+right", "history_redo", "Prompt fwd", show=False),
        Binding("ctrl+r", "run_selected", "Run case"),
        Binding("ctrl+s", "save", "Save harness"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cases: list[Case] = []
        self.history = PromptHistory()
        self._entries: dict[str, StudioEntry] = {}
        self._active_runs = 0
        self._manual_models: list[str] = []
        self._log: list[str] = []
        self._edit_timer: Timer | None = None
        self._saved_as: tuple[str, str] = ("", "")
        # Clean-state snapshot for is_dirty(): (prompt hash, cases) as last loaded/saved.
        self._baseline: tuple[str | None, list[Case]] = (None, [])
        self._unsaved_results = False

    # -- layout ---------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Horizontal(id="studio-top"):
            yield Select([], prompt="Model", id="model", tooltip="Model under test")
            yield Input(placeholder="provider:model (manual, Enter)", id="manual-model")
            yield Select([], prompt="Judge", id="judge", tooltip="Judge model (optional)")
            yield Input(placeholder="temp", id="temperature", tooltip="Temperature")
            yield Input(placeholder="max tok", id="max_tokens", tooltip="Max tokens")
            docs_as = Input(placeholder="documents", id="documents_name",
                            tooltip="Docs as: the template variable holding the case's "
                                    "documents (blank = documents)")
            docs_as.border_title = "Docs as"
            yield docs_as
        with Horizontal(id="studio-body"):
            with Vertical(id="studio-editors"):
                yield Label("System prompt")
                yield TextArea("", id="system")
                yield Label(_template_label("documents"), id="template-label")
                yield TextArea(DEFAULT_TEMPLATE, id="template")
            with Vertical(id="studio-side"):
                yield Label(HINT, id="studio-hint")
                yield Label("idle", id="studio-status")
                yield CaseList(id="cases")
                # min_width=1: the default (78) is wider than this pane in a ~125-column terminal.
                yield RichLog(id="output", wrap=True, markup=False, min_width=1)

    def on_mount(self) -> None:
        self.refresh_models()
        self.history.push(self._prompt_or_none() or PromptVersion(template=DEFAULT_TEMPLATE))
        self._mark_clean()

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        # When our tab is hidden, Textual hands focus to a still-"visible" sibling inside
        # this pane, which would make TabbedContent switch back to the studio. Swallow it.
        # Consequence: programmatic callers must activate the studio tab *before* focusing
        # a studio widget (focusing alone will not switch tabs).
        pane = next((a for a in self.ancestors if isinstance(a, TabPane)), None)
        tabs = next((a for a in self.ancestors if isinstance(a, TabbedContent)), None)
        if pane is not None and tabs is not None and tabs.active != pane.id:
            event.stop()
            focused = self.screen.focused
            if focused is not None and self in focused.ancestors:
                self.screen.set_focus(None)

    @property
    def db(self):
        return self.app.db  # type: ignore[attr-defined]

    @property
    def output_text(self) -> str:
        return "\n".join(self._log)

    def result_for(self, case_name: str) -> CaseResult | None:
        e = self._entries.get(case_name)
        return e.result if e else None

    def _write(self, text: Text | str) -> None:
        self._log.append(text.plain if isinstance(text, Text) else text)
        self.query_one("#output", RichLog).write(text)

    # -- models ---------------------------------------------------------
    def refresh_models(self) -> None:
        refs = [f"{p.name}:{m}" for p in self.db.list_providers() if p.enabled
                for m in self.db.list_models(p.name)]
        refs += [m for m in self._manual_models if m not in refs]
        # Re-populating fires Select.Changed; that is not a user change, so don't re-warn.
        with self.prevent(Select.Changed):
            for sel in self.query(Select).filter("#model, #judge"):
                keep = sel.value
                sel.set_options([(r, r) for r in refs])
                if keep in refs:
                    sel.value = keep

    def _ref(self, select_id: str) -> ModelRef | None:
        v = self.query_one(select_id, Select).value
        return ModelRef.parse(v) if isinstance(v, str) else None

    @on(Input.Submitted, "#manual-model")
    def _manual_model(self, event: Input.Submitted) -> None:
        raw = event.value.strip()
        try:
            ref = ModelRef.parse(raw)
            if not ref.provider or not ref.model:
                raise ValueError
        except ValueError:
            self.notify("Enter a model as provider:model", severity="error")
            return
        if str(ref) not in self._manual_models:
            self._manual_models.append(str(ref))
            self.refresh_models()
        self.query_one("#model", Select).value = str(ref)
        event.input.value = ""

    @on(Select.Changed, "#model, #judge")
    def _check_judge(self) -> None:
        judge, model = self._ref("#judge"), self._ref("#model")
        pair = (judge, model)
        if pair == getattr(self, "_warned_pair", None):
            return  # only warn on a real change
        self._warned_pair = pair
        if judge is not None and judge == model:
            self.notify("Judge model is the same as the model under test", severity="warning")

    # -- prompt + history ------------------------------------------------
    def current_prompt(self) -> PromptVersion:
        """Build the prompt from the editors; raises ValueError on bad parameters."""
        def num(field: str, label: str, cast):
            raw = self.query_one(field, Input).value.strip()
            if not raw:
                return None
            try:
                return cast(raw)
            except ValueError:
                raise ValueError(f"{label} must be a number, got {raw!r}") from None

        try:
            return PromptVersion(
                system=self.query_one("#system", TextArea).text,
                template=self.query_one("#template", TextArea).text,
                temperature=num("#temperature", "Temperature", float),
                max_tokens=num("#max_tokens", "Max tokens", int),
                documents_name=self.query_one("#documents_name", Input).value.strip()
                or "documents",
            )
        except ValidationError as e:  # a ValueError, but with a verbose pydantic message
            raise ValueError("; ".join(err["msg"].removeprefix("Value error, ")
                                       for err in e.errors())) from None

    def _prompt_or_none(self) -> PromptVersion | None:
        try:
            return self.current_prompt()
        except ValueError:
            return None

    def _snapshot(self) -> None:
        p = self._prompt_or_none()
        if p is not None:
            self.history.push(p)

    @on(Input.Changed, "#documents_name")
    def _update_template_label(self) -> None:
        raw = self.query_one("#documents_name", Input).value
        self.query_one("#template-label", Label).update(
            _template_label(_valid_documents_name(raw)))

    @on(TextArea.Changed, "#system, #template")
    @on(Input.Changed, "#temperature, #max_tokens, #documents_name")
    def _edited(self) -> None:
        if self._edit_timer is not None:
            self._edit_timer.stop()
        self._edit_timer = self.set_timer(self.HISTORY_PAUSE, self._snapshot)

    def _restore(self, p: PromptVersion | None) -> None:
        if p is None:
            self.notify("No further prompt history", severity="information")
            return
        self.query_one("#system", TextArea).text = p.system
        self.query_one("#template", TextArea).text = p.template
        self.query_one("#temperature", Input).value = "" if p.temperature is None else str(
            p.temperature)
        self.query_one("#max_tokens", Input).value = "" if p.max_tokens is None else str(
            p.max_tokens)
        self.query_one("#documents_name", Input).value = (
            "" if p.documents_name == "documents" else p.documents_name)
        # Callers may suppress Input.Changed (loading a harness), so update directly.
        self._update_template_label()

    def _step(self, forward: bool) -> None:
        if self._edit_timer is not None:
            self._edit_timer.stop()
        self._snapshot()
        self._restore(self.history.redo() if forward else self.history.undo())

    def action_history_undo(self) -> None:
        self._step(False)

    def action_history_redo(self) -> None:
        self._step(True)

    # -- cases ----------------------------------------------------------
    def _label(self, case: Case) -> str:
        e = self._entries.get(case.name)
        return f"[{e.result.status.upper()}] {case.name}" if e else f"[ - ] {case.name}"

    def _set_label(self, idx: int, text: str) -> None:
        lv = self.query_one("#cases", ListView)
        if 0 <= idx < len(lv.children):
            lv.children[idx].query_one(Label).update(text)

    def _selected_index(self) -> int | None:
        idx = self.query_one("#cases", ListView).index
        return idx if idx is not None and 0 <= idx < len(self.cases) else None

    async def add_case(self, case: Case) -> None:
        if any(c.name == case.name for c in self.cases):
            raise ValueError(f"duplicate case name: {case.name}")
        self.cases.append(case)
        lv = self.query_one("#cases", ListView)
        await lv.append(ListItem(Label(self._label(case))))
        lv.index = len(self.cases) - 1

    async def action_new_case(self) -> None:
        async def done(case: Case | None) -> None:
            if case is not None:
                await self.add_case(case)

        self.app.push_screen(CaseForm(None, {c.name for c in self.cases}), done)

    async def action_delete_case(self) -> None:
        idx = self._selected_index()
        if idx is None:
            self.notify("No case selected", severity="warning")
            return
        case = self.cases.pop(idx)
        self._entries.pop(case.name, None)
        await self.query_one("#cases", ListView).remove_items([idx])

    @on(ListView.Selected, "#cases")
    def _edit_selected(self) -> None:
        idx = self._selected_index()
        if idx is None:
            return
        old = self.cases[idx]

        def done(case: Case | None) -> None:
            if case is None or idx >= len(self.cases) or self.cases[idx] is not old:
                return
            self.cases[idx] = case
            self._entries.pop(old.name, None)  # result no longer matches the case
            self._set_label(idx, self._label(case))

        taken = {c.name for c in self.cases if c is not old}
        self.app.push_screen(CaseForm(old, taken), done)

    # -- running --------------------------------------------------------
    def _busy(self) -> bool:
        if self._active_runs:
            self.notify("A studio run is already running; wait for it to finish",
                        severity="warning")
            return True
        return False

    def _set_running(self, delta: int) -> None:
        self._active_runs = max(0, self._active_runs + delta)
        try:
            status = self.query_one("#studio-status", Label)
        except NoMatches:  # pane torn down (app quitting during a run)
            return
        status.update("● running…" if self._active_runs else "idle")

    async def action_run_selected(self) -> None:
        if self._busy():
            return
        idx = self._selected_index()
        if idx is None:
            self.notify("No case selected", severity="warning")
            return
        self._start_run([self.cases[idx]])

    async def action_run_all(self) -> None:
        if self._busy():
            return
        if not self.cases:
            self.notify("No cases to run; press n to add one", severity="warning")
            return
        self._start_run(list(self.cases))

    def _start_run(self, cases: list[Case]) -> None:
        model = self._ref("#model")
        if model is None:
            self.notify("Select a model (or enter provider:model) first", severity="error")
            return
        try:
            prompt = self.current_prompt()
        except ValueError as e:
            self.notify(str(e), severity="error")
            return
        judge = self._ref("#judge")
        if judge is not None and judge == model:
            self.notify("Judge model is the same as the model under test", severity="warning")
        self.history.push(prompt)
        harness = Harness(name="studio", prompt=prompt, cases=cases)
        for c in cases:
            self._set_label(self.cases.index(c), f"[ … ] {c.name}")
        self._set_running(+1)
        self.run_worker(self._run(harness, model, judge), name="studio-run",
                        exclusive=False, group="studio-run")

    async def _run(self, harness: Harness, model: ModelRef, judge: ModelRef | None) -> None:
        started = now_iso()
        ran = {c.name: c for c in harness.cases}  # the exact Case objects that were run

        def on_result(r: CaseResult) -> None:
            entry = StudioEntry(ran[r.case_name], r, harness.prompt.hash, model, judge,
                                started, now_iso(), r.status == "judge_error")
            self._record(entry)

        try:
            providers = {p.name: p for p in self.db.list_providers()}
            await run_harness(harness, model, providers, self.app.client,  # type: ignore[attr-defined]
                              judge_model=judge, on_result=on_result)
        except Exception as e:  # never crash the UI
            self._write(Text(f"run failed: {type(e).__name__}: {e}", style="bold red"))
            for c in harness.cases:
                if c in self.cases:
                    self._set_label(self.cases.index(c), self._label(c))
        finally:
            self._set_running(-1)

    def _record(self, entry: StudioEntry) -> None:
        r = entry.result
        idx = next((i for i, c in enumerate(self.cases) if c.name == r.case_name), None)
        if idx is None or self.cases[idx] != entry.case:
            # Case deleted or edited while in flight: the result no longer describes it.
            if idx is not None:
                self._set_label(idx, self._label(self.cases[idx]))
            self._write(Text(f"discarded result for {r.case_name!r}: case changed during run",
                             style="dim"))
            return
        self._entries[r.case_name] = entry
        self._unsaved_results = True
        self._set_label(idx, self._label(self.cases[idx]))
        self._write(format_result(r, entry.model))
