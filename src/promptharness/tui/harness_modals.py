"""Modal screens used by the Harnesses pane: regression setup, export and import."""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select, SelectionList

from promptharness.core.examples import PREFIX as EXAMPLE_PREFIX
from promptharness.core.examples import ExampleError, read_example
from promptharness.core.models import ModelRef
from promptharness.core.portable import (
    PortableError,
    export_harness,
    import_harness,
    parse_harness,
)
from promptharness.tui.studio_modals import ConfirmModal

RegressionChoice = tuple[list[ModelRef], ModelRef | None]


def _parse_refs(raw: str) -> list[ModelRef]:
    refs = []
    for part in raw.replace(",", " ").split():
        ref = ModelRef.parse(part)  # ValueError on a missing colon
        if not ref.provider or not ref.model:
            raise ValueError(part)
        refs.append(ref)
    return refs


class RegressionForm(ModalScreen["RegressionChoice | None"]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, harness: str, options: list[str]) -> None:
        super().__init__()
        self.harness_name = harness
        self.options = options

    def compose(self) -> ComposeResult:
        with Vertical(id="form", classes="compact"):
            yield Label(f"Regression run: {self.harness_name}")
            yield Label("Models to test (space toggles)")
            yield SelectionList(*((o, o) for o in self.options), id="regression-models")
            yield Label("Other models (provider:model, comma-separated)")
            yield Input(placeholder="provider:model, ...", id="regression-manual")
            yield Label("Judge model (optional, used for judge prompts)")
            yield Select([(o, o) for o in self.options], prompt="Judge: none",
                         id="regression-judge")
            yield Label("", id="regression-error", classes="form-error")
            with Horizontal(id="buttons"):
                yield Button("Run", id="regression-run", variant="primary")
                yield Button("Cancel", id="regression-cancel")

    def _error(self, msg: str) -> None:
        self.query_one("#regression-error", Label).update(msg)

    @on(Input.Submitted, "#regression-manual")
    @on(Button.Pressed, "#regression-run")
    def _submit(self) -> None:
        chosen = [ModelRef.parse(v) for v in self.query_one(SelectionList).selected]
        raw = self.query_one("#regression-manual", Input).value
        try:
            manual = _parse_refs(raw)
        except ValueError as e:
            self._error(f"Other models must be provider:model ({e})")
            return
        targets: list[ModelRef] = []
        for ref in chosen + manual:
            if ref not in targets:
                targets.append(ref)
        if not targets:
            self._error("Select at least one model (or type provider:model)")
            return
        jv = self.query_one("#regression-judge", Select).value
        judge = ModelRef.parse(jv) if isinstance(jv, str) else None
        if judge is not None and judge in targets:
            self.notify(f"Judge model {judge} is the same as a tested model",
                        severity="warning")
        self.dismiss((targets, judge))

    @on(Button.Pressed, "#regression-cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class ExportForm(ModalScreen["Path | None"]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, harness: str) -> None:
        super().__init__()
        self.harness_name = harness
        self._confirmed: Path | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="form", classes="compact"):
            yield Label(f"Export harness {self.harness_name!r}")
            yield Label("File path")
            yield Input(f"{self.harness_name}.yaml", id="export-path")
            yield Label("Format")
            yield Select([("YAML", "yaml"), ("JSON", "json")], value="yaml",
                         allow_blank=False, id="export-format")
            yield Checkbox("Inline document contents", False, id="export-inline")
            yield Label("", id="export-error", classes="form-error")
            with Horizontal(id="buttons"):
                yield Button("Export", id="export-submit", variant="primary")
                yield Button("Cancel", id="export-cancel")

    @on(Select.Changed, "#export-format")
    def _format_changed(self, event: Select.Changed) -> None:
        inp = self.query_one("#export-path", Input)
        stem, dot, ext = inp.value.rpartition(".")
        if dot and ext in ("yaml", "yml", "json") and isinstance(event.value, str):
            inp.value = f"{stem}.{event.value}"

    @on(Input.Submitted, "#export-path")
    @on(Button.Pressed, "#export-submit")
    def _submit(self) -> None:
        err = self.query_one("#export-error", Label)
        raw = self.query_one("#export-path", Input).value.strip()
        if not raw:
            err.update("File path is required")
            return
        path = Path(raw).expanduser()
        if path.exists() and self._confirmed != path:
            self._confirmed = path
            err.update(f"{path} exists; press Export again to overwrite it")
            return
        fmt = str(self.query_one("#export-format", Select).value)
        inline = self.query_one("#export-inline", Checkbox).value
        try:
            text = export_harness(self.app.db, self.harness_name, fmt,  # type: ignore[attr-defined]
                                  inline_documents=inline)
            path.write_text(text, encoding="utf-8")
        except PortableError as e:
            err.update(str(e))
            return
        except OSError as e:
            err.update(f"cannot write {path}: {e}")
            return
        self.dismiss(path)

    @on(Button.Pressed, "#export-cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class ImportForm(ModalScreen["Harness | None"]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="form", classes="compact"):
            yield Label("Import harness (YAML or JSON)")
            yield Label("File path, or example:NAME (try example:quickstart)")
            yield Input("", id="import-path", placeholder="path/to/harness.yaml or example:quickstart")
            yield Label("", id="import-error", classes="form-error")
            with Horizontal(id="buttons"):
                yield Button("Import", id="import-submit", variant="primary")
                yield Button("Cancel", id="import-cancel")

    def _error(self, msg: str) -> None:
        self.query_one("#import-error", Label).update(msg)

    @on(Input.Submitted, "#import-path")
    @on(Button.Pressed, "#import-submit")
    def _submit(self) -> None:
        raw = self.query_one("#import-path", Input).value.strip()
        if not raw:
            self._error("File path is required")
            return
        if raw.startswith(EXAMPLE_PREFIX):
            try:
                text = read_example(raw[len(EXAMPLE_PREFIX):].strip())
            except ExampleError as e:
                self._error(str(e))
                return
        else:
            path = Path(raw).expanduser()
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                self._error(f"cannot read {path}: {e}")
                return
        try:
            harness, _ = parse_harness(text)
            exists = self.app.db.get_harness(harness.name) is not None  # type: ignore[attr-defined]
        except PortableError as e:
            self._error(str(e))
            return
        except Exception as e:  # never crash the UI
            self._error(f"{type(e).__name__}: {e}")
            return
        if not exists:
            self._import(text, overwrite=False)
            return

        def confirmed(yes: bool | None) -> None:
            if yes:
                self._import(text, overwrite=True)
            else:
                self._error(f"harness '{harness.name}' exists; not imported")

        self.app.push_screen(
            ConfirmModal(f"Harness {harness.name!r} exists. Overwrite it?"), confirmed)

    def _import(self, text: str, overwrite: bool) -> None:
        try:
            saved = import_harness(self.app.db, text, overwrite=overwrite)  # type: ignore[attr-defined]
        except PortableError as e:
            self._error(str(e))
            return
        except Exception as e:  # never crash the UI
            self._error(f"{type(e).__name__}: {e}")
            return
        self.dismiss(saved)

    @on(Button.Pressed, "#import-cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
