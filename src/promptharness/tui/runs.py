"""Runs pane: history of stored runs with status counts; open one or several read-only,
or re-test a run on a replacement model."""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import DataTable

from promptharness.core.models import ModelRef, Run
from promptharness.core.retest import prompt_changed, retest
from promptharness.tui.matrix import MatrixScreen
from promptharness.tui.retest_modal import RetestForm

__all__ = ["RunsPane"]

COUNTED = ("pass", "fail", "manual", "error", "judge_error")


def _local_time(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return iso


class RunsPane(Widget):
    BINDINGS = [
        Binding("enter", "open", "Open run(s)"),
        Binding("space", "mark", "Mark for side-by-side"),
        Binding("r", "retest", "Re-test on model"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.marked: set[int] = set()
        self._runs: dict[int, Run] = {}

    def compose(self) -> ComposeResult:
        yield DataTable(id="runs-table", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        t = self.query_one(DataTable)
        t.add_column("✓", key="mark")
        t.add_columns("Time", "Harness", "Model", "Pass", "Fail", "Manual", "Error",
                      "Judge err")
        self.refresh_table()

    @property
    def db(self):
        return self.app.db  # type: ignore[attr-defined]

    def refresh_table(self) -> None:
        t = self.query_one(DataTable)
        keep = self._selected_id()
        t.clear()
        try:
            runs = self.db.list_runs()
        except Exception as e:  # never crash the UI
            self.notify(f"Could not load runs: {e}", severity="error")
            return
        self._runs = {r.id: r for r in runs if r.id is not None}
        self.marked &= set(self._runs)
        for r in runs:
            if r.id is None:
                continue
            counts = Counter(x.status for x in r.results)
            t.add_row("✓" if r.id in self.marked else "", _local_time(r.started_at),
                      r.harness, str(r.model), *(str(counts[s]) for s in COUNTED),
                      key=str(r.id))
        if keep is not None and keep in self._runs:
            t.move_cursor(row=t.get_row_index(str(keep)))

    def _selected_id(self) -> int | None:
        t = self.query_one(DataTable)
        if t.row_count == 0:
            return None
        try:
            key = t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value
        except Exception:
            return None
        return int(key) if key else None

    def action_mark(self) -> None:
        rid = self._selected_id()
        if rid is None:
            return
        self.marked ^= {rid}
        t = self.query_one(DataTable)
        t.update_cell(str(rid), "mark", "✓" if rid in self.marked else "")
        t.action_cursor_down()

    @on(DataTable.RowSelected, "#runs-table")
    def _row_selected(self) -> None:
        self.action_open()

    def action_open(self) -> None:
        ids = [i for i in self._runs if i in self.marked] or (
            [self._selected_id()] if self._selected_id() is not None else [])
        runs = [self._runs[i] for i in ids if i in self._runs]
        if not runs:
            self.notify("No run selected", severity="warning")
            return
        runs.sort(key=lambda r: (r.started_at, r.id or 0))
        # Verdicts can be set on stored runs: refresh the counts when the matrix closes.
        self.app.push_screen(MatrixScreen.from_runs(runs), lambda _=None: self.refresh_table())

    # -- re-test on replacement -------------------------------------------
    def _model_options(self) -> list[str]:
        return [f"{p.name}:{m}" for p in self.db.list_providers() if p.enabled
                for m in self.db.list_models(p.name)]

    def action_retest(self) -> None:
        rid = self._selected_id()
        run = self._runs.get(rid) if rid is not None else None
        if run is None:
            self.notify("No run selected", severity="warning")
            return
        try:
            options = self._model_options()
        except Exception as e:  # never crash: manual entry still works
            options = []
            self.notify(f"Could not load models: {e}", severity="error")

        def chosen(ref: ModelRef | None) -> None:
            if ref is not None:
                self.run_worker(self._retest(run, ref), name="retest", group="retest")

        self.app.push_screen(RetestForm(run, options), chosen)

    async def _retest(self, run: Run, ref: ModelRef) -> None:
        self.notify(f"Re-testing {run.harness!r} on {ref}…")
        try:
            if prompt_changed(self.db, run):
                self.notify("Note: harness prompt changed since the original run; "
                            "the re-test uses the current prompt", severity="warning")
            providers = {p.name: p for p in self.db.list_providers()}
            new = await retest(self.db, run, ref, providers,
                               self.app.client)  # type: ignore[attr-defined]
        except Exception as e:  # never crash the UI
            self.notify(f"Re-test failed: {e}", severity="error")
            return
        self.refresh_table()
        self.notify(f"Re-test finished: run #{new.id} ({ref})")
        self.app.push_screen(MatrixScreen.from_runs([new]), lambda _=None: self.refresh_table())
