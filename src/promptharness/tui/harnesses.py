"""Harnesses pane: list, open in Studio, regression run, duplicate, delete, export/import."""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import DataTable, TabbedContent

from promptharness.core.models import Harness, Run
from promptharness.tui.harness_modals import (
    ExportForm,
    ImportForm,
    RegressionChoice,
    RegressionForm,
)
from promptharness.tui.matrix import MatrixScreen
from promptharness.tui.studio_modals import ConfirmModal

__all__ = ["HarnessesPane", "copy_name"]


def copy_name(name: str, taken: set[str]) -> str:
    candidate = f"{name} copy"
    n = 2
    while candidate in taken:
        candidate = f"{name} copy {n}"
        n += 1
    return candidate


class HarnessesPane(Widget):
    BINDINGS = [
        Binding("enter", "open", "Open in Studio", show=True),
        Binding("m", "regression", "Regression run"),
        Binding("d", "duplicate", "Duplicate"),
        Binding("D", "delete", "Delete"),
        Binding("e", "export", "Export"),
        Binding("i", "import", "Import"),
    ]

    def compose(self) -> ComposeResult:
        yield DataTable(id="harnesses-table", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        t = self.query_one(DataTable)
        t.add_columns("Name", "Accepted model", "Cases")
        self.refresh_table()

    @property
    def db(self):
        return self.app.db  # type: ignore[attr-defined]

    def refresh_table(self, select: str | None = None) -> None:
        t = self.query_one(DataTable)
        keep = select or self._selected_name()
        t.clear()
        try:
            harnesses = self.db.list_harnesses()
        except Exception as e:  # never crash the UI
            self.notify(f"Could not load harnesses: {e}", severity="error")
            return
        for h in harnesses:
            t.add_row(h.name, str(h.accepted_model) if h.accepted_model else "-",
                      str(len(h.cases)), key=h.name)
        if keep is not None:
            try:
                t.move_cursor(row=t.get_row_index(keep))
            except Exception:
                pass

    def _selected_name(self) -> str | None:
        t = self.query_one(DataTable)
        if t.row_count == 0:
            return None
        try:
            return t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value
        except Exception:
            return None

    def _need_selection(self) -> Harness | None:
        name = self._selected_name()
        h = self.db.get_harness(name) if name else None
        if h is None:
            self.notify("No harness selected", severity="warning")
        return h

    # -- open in studio -------------------------------------------------
    @on(DataTable.RowSelected, "#harnesses-table")
    async def _row_selected(self) -> None:
        await self.action_open()

    async def action_open(self) -> None:
        from promptharness.tui.studio import StudioPane

        h = self._need_selection()
        if h is None:
            return
        studio = self.screen.query_one(StudioPane)
        if not await studio.load_harness(h, h.accepted_model):
            return
        # Activate the tab first: StudioPane swallows focus while its tab is hidden.
        self.screen.query_one(TabbedContent).active = "studio"
        cases = self.screen.query_one("#cases")
        self.call_after_refresh(cases.focus)

    # -- regression -----------------------------------------------------
    def _model_options(self) -> list[str]:
        return [f"{p.name}:{m}" for p in self.db.list_providers() if p.enabled
                for m in self.db.list_models(p.name)]

    def action_regression(self) -> None:
        h = self._need_selection()
        if h is None:
            return
        if not h.cases:
            self.notify(f"Harness {h.name!r} has no cases", severity="warning")
            return

        def chosen(choice: RegressionChoice | None) -> None:
            if choice is None:
                return
            targets, judge = choice
            self.app.push_screen(
                MatrixScreen(h, targets, judge, on_saved=self._run_saved),
                lambda _=None: self._refresh_runs())

        self.app.push_screen(RegressionForm(h.name, self._model_options()), chosen)

    def _run_saved(self, run: Run) -> None:
        self._refresh_runs()

    def _refresh_runs(self) -> None:
        from promptharness.tui.runs import RunsPane

        try:
            self.screen.query_one(RunsPane).refresh_table()
        except Exception:
            pass

    # -- duplicate / delete ---------------------------------------------
    def action_duplicate(self) -> None:
        h = self._need_selection()
        if h is None:
            return
        try:
            new = copy_name(h.name, {x.name for x in self.db.list_harnesses()})
            self.db.save_harness(h.model_copy(update={"name": new}, deep=True))
        except Exception as e:
            self.notify(f"Duplicate failed: {e}", severity="error")
            return
        self.refresh_table(select=new)
        self.notify(f"Created {new!r}")

    def action_delete(self) -> None:
        h = self._need_selection()
        if h is None:
            return

        def confirmed(yes: bool | None) -> None:
            if not yes:
                return
            try:
                self.db.delete_harness(h.name)
            except Exception as e:
                self.notify(f"Delete failed: {e}", severity="error")
                return
            self.refresh_table()
            self.notify(f"Deleted {h.name!r} (its runs stay in history)")

        self.app.push_screen(
            ConfirmModal(f"Delete harness {h.name!r}? Its runs stay in history."), confirmed)

    # -- export / import ------------------------------------------------
    def action_export(self) -> None:
        h = self._need_selection()
        if h is None:
            return

        def done(path: Path | None) -> None:
            if path is not None:
                self.notify(f"Exported {h.name!r} to {path}")

        self.app.push_screen(ExportForm(h.name), done)

    def action_import(self) -> None:
        def done(h: Harness | None) -> None:
            if h is not None:
                self.refresh_table(select=h.name)
                self._refresh_runs()
                self.notify(f"Imported {h.name!r}")

        self.app.push_screen(ImportForm(), done)
