"""Modal screens used by the Studio pane (case editor, save, confirm, verdict)."""

from __future__ import annotations

import json
import os
import re

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select, TextArea

from promptharness.core.models import Case, Expectation, Match

MATCH_MODES = [("No text match", "none"), ("Exact match", "exact"),
               ("Normalized match", "normalized")]


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


class CaseForm(ModalScreen["Case | None"]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, case: Case | None, taken: set[str]) -> None:
        super().__init__()
        self.existing = case
        self.taken = taken  # names of the *other* cases

    def compose(self) -> ComposeResult:
        c = self.existing or Case(name="")
        e = c.expectation
        mode = ("exact" if e.exact is not None
                else "normalized" if e.normalized is not None else "none")
        match_text = e.exact if e.exact is not None else e.normalized or ""
        with VerticalScroll(id="case-form"):
            yield Label("Edit case" if self.existing else "New case")
            yield Label("Name")
            yield Input(c.name, id="case-name")
            yield Label("Input (available to the template as {{ input }})")
            yield TextArea(c.input, id="case-input")
            yield Label("Document paths, comma-separated "
                        "(text, PDF, Word, PowerPoint, Excel, OpenDocument, RTF, images)")
            yield Input(", ".join(c.documents), id="case-docs")
            yield Label("Must include (one per line)")
            yield TextArea("\n".join(m.pattern for m in e.must_include), id="must-include")
            yield Label("Must not include (one per line)")
            yield TextArea("\n".join(m.pattern for m in e.must_not_include),
                           id="must-not-include")
            yield Checkbox("Treat include/exclude entries as regular expressions",
                           any(m.regex for m in e.must_include + e.must_not_include), id="regex")
            yield Label("Text match")
            yield Select(MATCH_MODES, value=mode, allow_blank=False, id="match-mode")
            yield TextArea(match_text, id="match-text")
            yield Checkbox("Output must be valid JSON", e.json_output, id="json-output")
            yield Label("JSON Schema (optional)")
            yield TextArea(json.dumps(e.json_schema, indent=2) if e.json_schema else "",
                           id="json-schema")
            yield Label("Judge prompt (optional; needs a judge model)")
            yield TextArea(e.judge_prompt or "", id="judge-prompt")
            with Horizontal(id="buttons"):
                yield Button("Save", id="case-submit", variant="primary")
                yield Button("Cancel", id="case-cancel")

    def _error(self, msg: str) -> None:
        self.notify(msg, severity="error")

    @on(Button.Pressed, "#case-submit")
    def _submit(self) -> None:
        q = self.query_one
        name = q("#case-name", Input).value.strip()
        if not name:
            return self._error("Case name is required")
        if name in self.taken:
            return self._error(f"A case named {name!r} already exists")
        regex = q("#regex", Checkbox).value
        include = _lines(q("#must-include", TextArea).text)
        exclude = _lines(q("#must-not-include", TextArea).text)
        if regex:
            for pat in include + exclude:
                try:
                    re.compile(pat)
                except re.error as e:
                    return self._error(f"Invalid regex {pat!r}: {e}")
        schema_text = q("#json-schema", TextArea).text.strip()
        schema = None
        if schema_text:
            try:
                schema = json.loads(schema_text)
            except json.JSONDecodeError as e:
                return self._error(f"JSON Schema is not valid JSON: {e}")
            if not isinstance(schema, dict):
                return self._error("JSON Schema must be a JSON object")
        mode = q("#match-mode", Select).value
        match_text = q("#match-text", TextArea).text
        judge = q("#judge-prompt", TextArea).text.strip()
        exp = Expectation(
            must_include=[Match(pattern=p, regex=regex) for p in include],
            must_not_include=[Match(pattern=p, regex=regex) for p in exclude],
            exact=match_text if mode == "exact" else None,
            normalized=match_text if mode == "normalized" else None,
            json_output=q("#json-output", Checkbox).value,
            json_schema=schema,
            judge_prompt=judge or None,
        )
        # Normalise on entry so documents resolve the same regardless of the cwd at run time.
        docs = [os.path.abspath(os.path.expanduser(d.strip()))
                for d in q("#case-docs", Input).value.split(",") if d.strip()]
        self.dismiss(Case(
            name=name,
            input=q("#case-input", TextArea).text,
            documents=docs,
            notes=self.existing.notes if self.existing else "",
            expectation=exp,
        ))

    @on(Button.Pressed, "#case-cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class SaveForm(ModalScreen["tuple[str, str] | None"]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, name: str = "", description: str = "") -> None:
        super().__init__()
        self.initial = (name, description)

    def compose(self) -> ComposeResult:
        with Vertical(id="form"):
            yield Label("Save as harness")
            yield Label("Name")
            yield Input(self.initial[0], id="save-name")
            yield Label("Description")
            yield Input(self.initial[1], id="save-description")
            with Horizontal(id="buttons"):
                yield Button("Save", id="save-submit", variant="primary")
                yield Button("Cancel", id="save-cancel")

    @on(Input.Submitted)
    @on(Button.Pressed, "#save-submit")
    def _submit(self) -> None:
        name = self.query_one("#save-name", Input).value.strip()
        if not name:
            self.notify("Harness name is required", severity="error")
            return
        self.dismiss((name, self.query_one("#save-description", Input).value.strip()))

    @on(Button.Pressed, "#save-cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmModal(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "no", "No"), Binding("y", "yes", "Yes"),
                Binding("n", "no", "No")]

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="form"):
            yield Label(self.message)
            with Horizontal(id="buttons"):
                yield Button("Yes", id="confirm-yes", variant="warning")
                yield Button("No", id="confirm-no")

    @on(Button.Pressed, "#confirm-yes")
    def action_yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#confirm-no")
    def action_no(self) -> None:
        self.dismiss(False)


class VerdictModal(ModalScreen["str | None"]):
    """Returns "pass", "fail", "clear", or None (cancelled)."""

    BINDINGS = [
        Binding("y", "pick('pass')", "Pass"),
        Binding("n", "pick('fail')", "Fail"),
        Binding("c", "pick('clear')", "Clear"),
        Binding("escape", "pick(None)", "Cancel"),
    ]

    def __init__(self, case_name: str) -> None:
        super().__init__()
        self.case_name = case_name

    def compose(self) -> ComposeResult:
        with Vertical(id="form"):
            yield Label(f"Manual verdict for {self.case_name!r}")
            yield Label("y = pass   n = fail   c = clear   esc = cancel")

    def action_pick(self, value: str | None) -> None:
        self.dismiss(value)
