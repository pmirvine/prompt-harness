"""Modal for re-test on replacement: pick a different provider:model for a stored run."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select

from promptharness.core.models import ModelRef, Run
from promptharness.core.retest import retest_config

__all__ = ["RetestForm"]


class RetestForm(ModalScreen["ModelRef | None"]):
    """Returns the replacement model, or None when cancelled. A typed model wins over the
    list; the original run's model is rejected inline."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, run: Run, options: list[str]) -> None:
        super().__init__()
        self.source_run = run
        self.options = options

    def compose(self) -> ComposeResult:
        run = self.source_run
        with Vertical(id="form", classes="compact"):
            yield Label(f"Re-test {run.harness!r} (run #{run.id}, {run.model})")
            judge = f"judge {run.judge_model}" if run.judge_model else "no judge"
            yield Label(f"Same harness and {judge}; the harness's current prompt is used.")
            yield Label("Replacement model")
            yield Select([(o, o) for o in self.options], prompt="Pick a model",
                         id="retest-model")
            yield Label("Or type provider:model")
            yield Input(placeholder="provider:model", id="retest-manual")
            yield Label("", id="retest-error", classes="form-error")
            with Horizontal(id="buttons"):
                yield Button("Re-test", id="retest-run", variant="primary")
                yield Button("Cancel", id="retest-cancel")

    def _error(self, msg: str) -> None:
        self.query_one("#retest-error", Label).update(msg)

    def _chosen(self) -> ModelRef | None:
        raw = self.query_one("#retest-manual", Input).value.strip()
        if raw:
            ref = ModelRef.parse(raw)  # ValueError on a missing colon
            if not ref.provider or not ref.model:
                raise ValueError(raw)
            return ref
        value = self.query_one("#retest-model", Select).value
        return ModelRef.parse(value) if isinstance(value, str) else None

    @on(Input.Submitted, "#retest-manual")
    @on(Button.Pressed, "#retest-run")
    def _submit(self) -> None:
        try:
            ref = self._chosen()
        except ValueError:
            self._error("Model must be provider:model")
            return
        if ref is None:
            self._error("Pick a model (or type provider:model)")
            return
        try:
            retest_config(self.source_run, ref)
        except ValueError:
            self._error(f"{ref} is the same model as the original run; pick a different one")
            return
        self.dismiss(ref)

    @on(Button.Pressed, "#retest-cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
