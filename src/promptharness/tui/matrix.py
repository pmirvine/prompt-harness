"""Regression matrix (case x model) screen and the per-cell result detail view."""

from __future__ import annotations

from collections.abc import Callable

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label, Static

from promptharness.core.models import CaseResult, Harness, ModelRef, Run
from promptharness.core.runner import run_harness
from promptharness.core.status import final_status
from promptharness.tui.compare import CompareScreen
from promptharness.tui.studio_modals import VerdictModal
from promptharness.tui.studio_support import STATUS_STYLE, format_result, now_iso

PENDING = "…"

__all__ = ["MatrixScreen", "ResultDetail"]


def _cell(status: str) -> Text:
    if status == PENDING:
        return Text(PENDING, style="dim")
    return Text(status, style=STATUS_STYLE.get(status, "bold"))


class ResultDetail(Screen):
    """Full detail for one matrix cell; `v` sets the manual verdict, `c` compares models.

    Read-only (stored-run) cells accept verdicts too: they are written straight to the
    database. Only a result without a database id cannot take one in read-only mode."""

    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("v", "verdict", "Verdict"),
        Binding("c", "compare", "Compare"),
    ]

    def __init__(self, matrix: MatrixScreen, case: str, col: str) -> None:
        super().__init__()
        self.matrix = matrix
        self.case = case
        self.col = col

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label(f"{self.case} × {self.matrix.column_label(self.col)}", id="detail-title")
        with VerticalScroll(id="detail-scroll"):
            yield Static(id="detail")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_text()
        self.query_one("#detail-scroll").focus()

    @property
    def text(self) -> str:
        return self._text.plain

    def refresh_text(self) -> None:
        r = self.matrix.results.get((self.case, self.col))
        if r is None:
            self._text = Text("no result yet", style="dim")
        else:
            self._text = format_result(r, self.matrix.column_model(self.col))
            self._text.append(f"\n\nprompt hash {self.matrix.prompt_hash(self.col)}",
                              style="dim")
        self.query_one("#detail", Static).update(self._text)

    def action_back(self) -> None:
        self.dismiss()

    def action_compare(self) -> None:
        self.matrix.open_compare(self.case)

    def action_verdict(self) -> None:
        r = self.matrix.results.get((self.case, self.col))
        if r is None:
            self.notify("No result for this cell yet", severity="warning")
            return
        if self.matrix.read_only and r.id is None:
            self.notify("Read-only view: this result is not stored, so it takes no verdict",
                        severity="warning")
            return

        def done(choice: str | None) -> None:
            if choice is None:
                return
            verdict = {"pass": True, "fail": False, "clear": None}[choice]
            self.matrix.apply_verdict(self.case, self.col, verdict)
            self.refresh_text()

        self.app.push_screen(VerdictModal(self.case), done)


class MatrixScreen(Screen):
    """Case x model matrix. Live mode runs the harness against each target; read-only mode
    (from_runs) shows stored runs side by side."""

    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("c", "compare", "Compare case"),
    ]

    def __init__(
        self,
        harness: Harness | None,
        targets: list[ModelRef],
        judge: ModelRef | None = None,
        *,
        runs: list[Run] | None = None,
        on_saved: Callable[[Run], None] | None = None,
    ) -> None:
        super().__init__()
        self.harness = harness
        self.judge = judge
        self.read_only = runs is not None
        self.on_saved = on_saved
        self.running = False
        self.results: dict[tuple[str, str], CaseResult] = {}
        self._judge_error: dict[tuple[str, str], bool] = {}
        self._models: dict[str, ModelRef] = {}
        self._labels: dict[str, str] = {}
        self._hashes: dict[str, str] = {}
        self.saved_runs: dict[str, Run] = {}
        self.rows: list[str] = []
        self.column_keys: list[str] = []
        if runs is not None:
            for run in runs:
                key = f"{run.model}#{run.id}"
                self._add_column(key, f"{run.model} #{run.id}", run.model, run.prompt_hash)
                self.saved_runs[key] = run
                for r in run.results:
                    if r.case_name not in self.rows:
                        self.rows.append(r.case_name)
                    self.results[(r.case_name, key)] = r
        else:
            assert harness is not None
            self.rows = [c.name for c in harness.cases]
            for t in targets:
                if str(t) not in self._models:
                    self._add_column(str(t), str(t), t, harness.prompt.hash)

    @classmethod
    def from_runs(cls, runs: list[Run]) -> MatrixScreen:
        return cls(None, [], runs=runs)

    def _add_column(self, key: str, label: str, model: ModelRef, prompt_hash: str) -> None:
        self.column_keys.append(key)
        self._labels[key] = label
        self._models[key] = model
        self._hashes[key] = prompt_hash

    def column_label(self, key: str) -> str:
        return self._labels[key]

    def column_model(self, key: str) -> ModelRef:
        return self._models[key]

    def prompt_hash(self, key: str) -> str:
        return self._hashes[key]

    # -- layout ---------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header()
        yield Label(self._title(), id="matrix-title")
        yield DataTable(id="matrix", cursor_type="cell", zebra_stripes=True)
        yield Label("", id="matrix-status")
        yield Footer()

    def _title(self) -> str:
        if self.read_only:
            names = sorted({r.harness for r in self.saved_runs.values()})
            return (f"Runs (read-only): {', '.join(names)}"
                    "  ·  enter = details, c = compare, esc = back")
        judge = f"  judge {self.judge}" if self.judge else ""
        return (f"Regression: {self.harness.name}{judge}"  # type: ignore[union-attr]
                "  ·  enter = details, c = compare, esc = back")

    def on_mount(self) -> None:
        t = self.query_one("#matrix", DataTable)
        t.add_column("Case", key="__case__")
        for key in self.column_keys:
            t.add_column(self._labels[key], key=key)
        for case in self.rows:
            cells = [self.results.get((case, k)) for k in self.column_keys]
            t.add_row(case, *(_cell(r.status if r else ("-" if self.read_only else PENDING))
                              for r in cells), key=case)
        t.focus()
        if self.read_only:
            self._status("")
        else:
            self.running = True
            self._status("running…")
            self.run_worker(self._run(), name="matrix-run", group="matrix-run",
                            exclusive=True)

    def _status(self, text: str) -> None:
        self.query_one("#matrix-status", Label).update(text)

    @property
    def db(self):
        return self.app.db  # type: ignore[attr-defined]

    # -- running --------------------------------------------------------
    def _set(self, case: str, key: str, r: CaseResult) -> None:
        self.results[(case, key)] = r
        self._update_cell(case, key)

    def _update_cell(self, case: str, key: str) -> None:
        r = self.results.get((case, key))
        try:
            self.query_one("#matrix", DataTable).update_cell(
                case, key, _cell(r.status if r else PENDING))
        except Exception:
            pass  # screen torn down

    async def _run(self) -> None:
        harness = self.harness
        assert harness is not None
        try:
            providers = {p.name: p for p in self.db.list_providers()}
        except Exception as e:
            providers = {}
            self.notify(f"Could not load providers: {e}", severity="error")
        for key in self.column_keys:
            target = self._models[key]
            started = now_iso()

            def on_result(r: CaseResult, key: str = key) -> None:
                self._judge_error[(r.case_name, key)] = r.status == "judge_error"
                self._set(r.case_name, key, r)

            try:
                run = await run_harness(harness, target, providers,
                                        self.app.client,  # type: ignore[attr-defined]
                                        judge_model=self.judge, on_result=on_result)
            except Exception as e:  # never crash or hang: fill the column with errors
                msg = f"run failed: {type(e).__name__}: {e}"
                results = []
                for case in self.rows:
                    r = self.results.get((case, key))
                    if r is None:
                        r = CaseResult(case_name=case, status="error", error=msg)
                        self._judge_error[(case, key)] = False
                        self._set(case, key, r)
                    results.append(r)
                run = Run(harness=harness.name, prompt_hash=harness.prompt.hash,
                          model=target, judge_model=self.judge, started_at=started,
                          finished_at=now_iso(), results=results)
            # Use our result objects (verdicts may have been set while running).
            run.results = [self.results.get((r.case_name, key), r) for r in run.results]
            self._save(key, run)
        self.running = False
        self._status(f"done · {len(self.saved_runs)} run(s) saved")
        self.notify(f"Regression finished: {len(self.saved_runs)} run(s) saved")

    def _save(self, key: str, run: Run) -> None:
        """Save `run` with its raw statuses, then re-apply any verdicts set before saving."""
        copy = run.model_copy(deep=True)
        verdicts: list[tuple[CaseResult, bool]] = []
        for live, raw in zip(run.results, copy.results, strict=True):
            if live.manual_verdict is not None:
                verdicts.append((live, live.manual_verdict))
            je = self._judge_error.get((live.case_name, key), False)
            raw.manual_verdict = None
            raw.status = final_status(raw.checks, raw.error, je, None)
        try:
            run_id = self.db.save_run(copy)
            for live, raw in zip(run.results, copy.results, strict=True):
                live.id = raw.id
            run.id = run_id
            for live, verdict in verdicts:
                saved = self.db.set_manual_verdict(live.id, verdict)
                live.status, live.manual_verdict = saved.status, saved.manual_verdict
        except Exception as e:
            self.notify(f"Could not save run for {self._labels[key]}: {e}", severity="error")
            return
        self.saved_runs[key] = run
        for live in run.results:
            self._update_cell(live.case_name, key)
        if self.on_saved is not None:
            try:
                self.on_saved(run)
            except Exception:
                pass

    # -- verdicts -------------------------------------------------------
    def apply_verdict(self, case: str, key: str, verdict: bool | None) -> None:
        r = self.results.get((case, key))
        if r is None or (self.read_only and r.id is None):
            return
        if r.id is not None:
            try:
                saved = self.db.set_manual_verdict(r.id, verdict)
            except Exception as e:
                self.notify(f"Could not save verdict: {e}", severity="error")
                return
            r.status, r.manual_verdict = saved.status, saved.manual_verdict
        else:  # not saved yet: kept in memory, persisted when the model's run is saved
            r.manual_verdict = verdict
            r.status = final_status(r.checks, r.error,
                                    self._judge_error.get((case, key), False), verdict)
        self._update_cell(case, key)

    # -- compare --------------------------------------------------------
    def _accepted_column(self, case: str) -> tuple[str, CaseResult] | None:
        """The accepted run's result for `case`, via harness.accepted_run_id (a duplicated
        harness shares the original's accepted run, so run.harness may differ)."""
        if self.harness is not None:
            candidates: list[Harness | None] = [self.harness]
        else:
            names = dict.fromkeys(r.harness for r in self.saved_runs.values())
            candidates = [self.db.get_harness(n) for n in names]
        for h in candidates:
            if h is None or h.accepted_run_id is None:
                continue
            run = self.db.get_run(h.accepted_run_id)
            if run is None:
                continue
            for r in run.results:
                if r.case_name == case:
                    return f"accepted: {run.model}", r
        return None

    def compare_columns(self, case: str) -> list[tuple[str, CaseResult | None]]:
        cols: list[tuple[str, CaseResult | None]] = []
        try:
            accepted = self._accepted_column(case)
        except Exception as e:  # never crash: compare without the accepted column
            accepted = None
            self.notify(f"Could not load the accepted run: {e}", severity="warning")
        if accepted is not None:
            cols.append(accepted)
        cols.extend((self._labels[k], self.results.get((case, k))) for k in self.column_keys)
        return cols

    def open_compare(self, case: str) -> None:
        self.app.push_screen(CompareScreen(case, self.compare_columns(case)))

    def action_compare(self) -> None:
        t = self.query_one("#matrix", DataTable)
        if t.row_count == 0:
            self.notify("No case to compare", severity="warning")
            return
        try:
            case = t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value
        except Exception:
            case = None
        if case is None:
            self.notify("No case highlighted", severity="warning")
            return
        self.open_compare(case)

    # -- navigation -----------------------------------------------------
    def open_detail(self, case: str, key: str) -> None:
        self.app.push_screen(ResultDetail(self, case, key))

    @on(DataTable.CellSelected, "#matrix")
    def _cell_selected(self, event: DataTable.CellSelected) -> None:
        case = event.cell_key.row_key.value
        key = event.cell_key.column_key.value
        if case is None or key is None or key == "__case__":
            return
        if (case, key) not in self.results:
            self.notify("No result for this cell yet", severity="warning")
            return
        self.open_detail(case, key)

    def action_back(self) -> None:
        if self.running:
            self.notify("Regression cancelled; finished models were saved",
                        severity="warning")
        self.dismiss()
