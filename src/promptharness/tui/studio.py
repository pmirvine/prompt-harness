"""Prompt Studio: edit a prompt, run it against in-memory cases, save as a harness."""

from __future__ import annotations

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
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

from promptharness.core.models import Case, CaseResult, Harness, ModelRef, PromptVersion, Run
from promptharness.core.runner import run_harness
from promptharness.core.status import final_status
from promptharness.tui.studio_modals import CaseForm, ConfirmModal, SaveForm, VerdictModal
from promptharness.tui.studio_support import (
    STATUS_STYLE,
    PromptHistory,
    _Entry,
    _now,
    format_result,
)

__all__ = ["PromptHistory", "StudioPane"]

DEFAULT_TEMPLATE = "{{ input }}"


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


class StudioPane(Widget):
    HISTORY_PAUSE = 2.0
    BINDINGS = [
        Binding("ctrl+z", "history_undo", "Prompt back"),
        Binding("ctrl+y", "history_redo", "Prompt fwd"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cases: list[Case] = []
        self.history = PromptHistory()
        self._entries: dict[str, _Entry] = {}
        self._manual_models: list[str] = []
        self._log: list[str] = []
        self._edit_timer: Timer | None = None
        self._saved_as: tuple[str, str] = ("", "")

    # -- layout ---------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Horizontal(id="studio-top"):
            yield Select([], prompt="Model under test", id="model")
            yield Input(placeholder="provider:model (manual, Enter)", id="manual-model")
            yield Select([], prompt="Judge: none", id="judge")
            yield Input(placeholder="temperature", id="temperature")
            yield Input(placeholder="max tokens", id="max_tokens")
        with Horizontal(id="studio-body"):
            with Vertical(id="studio-editors"):
                yield Label("System prompt")
                yield TextArea("", id="system")
                yield Label("User template (Jinja2: input, documents)")
                yield TextArea(DEFAULT_TEMPLATE, id="template")
            with Vertical(id="studio-side"):
                yield Label("Cases  n new · x delete · enter edit · r run · R run all · "
                            "v verdict · s save")
                yield CaseList(id="cases")
                yield RichLog(id="output", wrap=True, markup=False)

    def on_mount(self) -> None:
        self.refresh_models()
        self.history.push(self._prompt_or_none() or PromptVersion(template=DEFAULT_TEMPLATE))

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        # When our tab is hidden, Textual hands focus to a still-"visible" sibling inside
        # this pane, which would make TabbedContent switch back to the studio. Swallow it.
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
        judge = self._ref("#judge")
        if judge is not None and judge == self._ref("#model"):
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

        return PromptVersion(
            system=self.query_one("#system", TextArea).text,
            template=self.query_one("#template", TextArea).text,
            temperature=num("#temperature", "Temperature", float),
            max_tokens=num("#max_tokens", "Max tokens", int),
        )

    def _prompt_or_none(self) -> PromptVersion | None:
        try:
            return self.current_prompt()
        except ValueError:
            return None

    def _snapshot(self) -> None:
        p = self._prompt_or_none()
        if p is not None:
            self.history.push(p)

    @on(TextArea.Changed, "#system, #template")
    @on(Input.Changed, "#temperature, #max_tokens")
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
    async def action_run_selected(self) -> None:
        idx = self._selected_index()
        if idx is None:
            self.notify("No case selected", severity="warning")
            return
        self._start_run([self.cases[idx]])

    async def action_run_all(self) -> None:
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
        self.run_worker(self._run(harness, model, judge), exclusive=False, group="studio-run")

    async def _run(self, harness: Harness, model: ModelRef, judge: ModelRef | None) -> None:
        started = _now()

        def on_result(r: CaseResult) -> None:
            entry = _Entry(r, harness.prompt.hash, model, judge, started, _now(),
                           r.status == "judge_error")
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

    def _record(self, entry: _Entry) -> None:
        r = entry.result
        idx = next((i for i, c in enumerate(self.cases) if c.name == r.case_name), None)
        if idx is not None:
            self._entries[r.case_name] = entry
            self._set_label(idx, self._label(self.cases[idx]))
        self._write(format_result(r, entry.model))

    # -- verdict ----------------------------------------------------------
    async def action_verdict(self) -> None:
        idx = self._selected_index()
        entry = self._entries.get(self.cases[idx].name) if idx is not None else None
        if entry is None:
            self.notify("No result for the selected case; run it first", severity="warning")
            return

        def done(choice: str | None) -> None:
            if choice is None:
                return
            verdict = {"pass": True, "fail": False, "clear": None}[choice]
            self._apply_verdict(entry, verdict)

        self.app.push_screen(VerdictModal(entry.result.case_name), done)

    def _apply_verdict(self, entry: _Entry, verdict: bool | None) -> None:
        r = entry.result
        if r.id is not None:
            try:
                saved = self.db.set_manual_verdict(r.id, verdict)
            except Exception as e:
                self.notify(f"Could not save verdict: {e}", severity="error")
                return
            r.status, r.manual_verdict = saved.status, saved.manual_verdict
        else:
            r.manual_verdict = verdict
            r.status = final_status(r.checks, r.error, entry.judge_error, verdict)
        if r.case_name in self._entries:
            idx = next(i for i, c in enumerate(self.cases) if c.name == r.case_name)
            self._set_label(idx, self._label(self.cases[idx]))
        self._write(Text(f"verdict: {r.case_name} → {r.status.upper()}",
                         style=STATUS_STYLE.get(r.status, "bold")))

    # -- save -----------------------------------------------------------
    async def action_save(self) -> None:
        try:
            self.current_prompt()
        except ValueError as e:
            self.notify(str(e), severity="error")
            return

        def chosen(answer: tuple[str, str] | None) -> None:
            if answer is None:
                return
            name, desc = answer
            if self.db.get_harness(name) is None:
                self._save(name, desc)
                return

            def confirmed(yes: bool | None) -> None:
                if yes:
                    self._save(name, desc)

            self.app.push_screen(
                ConfirmModal(f"Harness {name!r} exists. Overwrite it?"), confirmed)

        self.app.push_screen(SaveForm(*self._saved_as), chosen)

    def _save(self, name: str, description: str) -> None:
        try:
            prompt = self.current_prompt()
            model = self._ref("#model")
            harness = Harness(name=name, description=description, prompt=prompt,
                              cases=list(self.cases), accepted_model=model)
            entries = [self._entries[c.name] for c in self.cases if c.name in self._entries]
            fresh = [e for e in entries if model is not None and e.model == model
                     and e.prompt_hash == prompt.hash]
            self.db.save_harness(harness)
            self._saved_as = (name, description)
            if not fresh:
                self.notify(f"Saved {name!r}; no results for the current prompt and model, "
                            "so no accepted run was stored", severity="warning")
                return
            run = Run(harness=name, prompt_hash=prompt.hash, model=model,
                      judge_model=fresh[0].judge,
                      started_at=min(e.started_at for e in fresh),
                      finished_at=max(e.finished_at for e in fresh),
                      results=[e.result for e in fresh])
            run_id = self.db.save_run(run)  # assigns result ids, enabling db verdicts
            self.db.set_accepted(name, model, run_id)
        except Exception as e:  # never crash the UI
            self.notify(f"Save failed: {type(e).__name__}: {e}", severity="error")
            return
        skipped = len(self.cases) - len(fresh)
        extra = f" ({skipped} case(s) without current results not included)" if skipped else ""
        self.notify(f"Saved harness {name!r} with accepted run #{run_id}{extra}")
