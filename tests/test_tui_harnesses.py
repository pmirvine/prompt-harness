"""TUI tests for the Harnesses pane, regression matrix and Runs pane."""

from __future__ import annotations

import asyncio

from conftest import FakeClient
from textual.widgets import (
    Button,
    DataTable,
    Input,
    ListView,
    Select,
    SelectionList,
    TabbedContent,
)

from promptharness.core.client import ClientError
from promptharness.core.db import Database
from promptharness.core.models import (
    Case,
    Expectation,
    Harness,
    Match,
    ModelRef,
    PromptVersion,
    Provider,
)
from promptharness.tui.app import PromptHarnessApp
from promptharness.tui.matrix import MatrixScreen, ResultDetail
from promptharness.tui.studio import StudioPane


def make_db(tmp_path) -> Database:
    db = Database(tmp_path / "tui.db")
    db.save_provider(Provider(name="p", base_url="https://p.test", api_key_env="K"))
    db.save_models("p", ["m1", "m2"])
    db.save_provider(Provider(name="off", base_url="https://o.test", api_key_env="K",
                              enabled=False))
    db.save_models("off", ["hidden"])
    return db


def ok_case(name: str, text: str) -> Case:
    return Case(name=name, input=text,
                expectation=Expectation(must_include=[Match(pattern="ok")]))


def make_harness(name: str = "h", accepted: str | None = "p:m1") -> Harness:
    return Harness(
        name=name,
        description="demo",
        prompt=PromptVersion(system="be brief", template="Q: {{ input }}"),
        cases=[ok_case("c1", "one"), ok_case("c2", "two")],
        accepted_model=ModelRef.parse(accepted) if accepted else None,
    )


def by_model(answers: dict[str, dict[str, object]]):
    """Callable FakeClient entry: answer keyed on (model, case input)."""
    def item(provider, model, messages, params):
        user = messages[-1]["content"]
        for needle, ans in answers[model].items():
            if needle in user:
                if isinstance(ans, Exception):
                    raise ans
                return ans
        raise AssertionError(f"no answer for {model} / {user}")
    return item


async def settle(app, pilot) -> None:
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def until(pilot, predicate, tries: int = 100) -> None:
    for _ in range(tries):
        if predicate():
            return
        await pilot.pause()
    raise AssertionError("condition not reached")


async def open_modal(app, pilot, key: str, selector: str) -> None:
    await pilot.press(key)
    await until(pilot, lambda: bool(app.screen.query(selector)))


async def press(app, pilot, selector: str) -> None:
    """Press a button directly (a real click is ignored while its 0.2s active effect runs)."""
    app.screen.query_one(selector, Button).press()
    await pilot.pause()


def cell(screen: MatrixScreen, case: str, col: str) -> str:
    return screen.query_one("#matrix", DataTable).get_cell(case, col).plain


async def focus_harnesses(app, pilot) -> DataTable:
    await pilot.press("1")
    await pilot.pause()
    t = app.query_one("#harnesses-table", DataTable)
    t.focus()
    await pilot.pause()
    return t


async def start_regression(app, pilot, models: list[str], judge: str | None = None) -> None:
    await focus_harnesses(app, pilot)
    await open_modal(app, pilot, "m", "#regression-models")
    sl = app.screen.query_one("#regression-models", SelectionList)
    for m in models:
        sl.select(m)
    if judge is not None:
        app.screen.query_one("#regression-judge", Select).value = judge
    await pilot.pause()
    await pilot.click("#regression-run")
    await settle(app, pilot)


# ---------------------------------------------------------------- harnesses list


async def test_harnesses_table_lists_harnesses(tmp_path):
    db = make_db(tmp_path)
    db.save_harness(make_harness("h"))
    db.save_harness(make_harness("other", accepted=None))
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.focused is app.query_one("#harnesses-table")  # landing focus
        t = await focus_harnesses(app, pilot)
        assert t.row_count == 2
        assert [str(c) for c in t.get_row("h")] == ["h", "p:m1", "2"]
        assert app.focused is t


async def test_enter_opens_harness_in_studio(tmp_path):
    db = make_db(tmp_path)
    db.save_harness(make_harness("h", accepted="p:m2"))
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test() as pilot:
        await focus_harnesses(app, pilot)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert app.query_one(TabbedContent).active == "studio"
        studio = app.query_one(StudioPane)
        assert [c.name for c in studio.cases] == ["c1", "c2"]
        assert studio.current_prompt().template == "Q: {{ input }}"
        assert studio.current_prompt().system == "be brief"
        assert app.query_one("#model", Select).value == "p:m2"
        assert app.focused is app.query_one("#cases", ListView)
        assert studio._saved_as == ("h", "demo")


async def test_studio_load_harness_with_unlisted_model_and_clears_results(tmp_path):
    db = make_db(tmp_path)
    app = PromptHarnessApp(db=db, client=FakeClient(["ok"]))
    async with app.run_test() as pilot:
        await pilot.press("2")
        await pilot.pause()
        studio = app.query_one(StudioPane)
        app.query_one("#model", Select).value = "p:m1"
        await studio.add_case(Case(name="old"))
        await studio.action_run_all()
        await settle(app, pilot)
        assert studio.result_for("old") is not None
        await studio.load_harness(make_harness("h", accepted="gone:x"), ModelRef.parse("gone:x"))
        await pilot.pause()
        assert [c.name for c in studio.cases] == ["c1", "c2"]
        assert studio.result_for("old") is None
        assert studio.output_text == "" or "loaded" in studio.output_text.lower()
        assert app.query_one("#model", Select).value == "gone:x"


async def test_duplicate_harness_creates_copy_named_with_suffix(tmp_path):
    db = make_db(tmp_path)
    db.save_harness(make_harness("h"))
    db.save_harness(make_harness("h copy"))
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test() as pilot:
        t = await focus_harnesses(app, pilot)
        t.move_cursor(row=t.get_row_index("h"))
        await pilot.press("d")
        await pilot.pause()
        names = [h.name for h in db.list_harnesses()]
        assert "h copy 2" in names
        copy = db.get_harness("h copy 2")
        assert [c.name for c in copy.cases] == ["c1", "c2"]
        assert copy.prompt == make_harness().prompt
        assert t.row_count == 3


async def test_delete_harness_asks_for_confirmation(tmp_path):
    db = make_db(tmp_path)
    db.save_harness(make_harness("h"))
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test() as pilot:
        t = await focus_harnesses(app, pilot)
        await pilot.press("D")
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert db.get_harness("h") is not None
        await pilot.press("D")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert db.get_harness("h") is None
        assert t.row_count == 0


async def test_export_writes_file_and_shows_errors_inline(tmp_path):
    db = make_db(tmp_path)
    h = make_harness("h")
    h.cases[0].documents = [str(tmp_path / "missing.txt")]
    db.save_harness(h)
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test() as pilot:
        await focus_harnesses(app, pilot)
        await open_modal(app, pilot, "e", "#export-path")
        out = tmp_path / "out.json"
        app.screen.query_one("#export-path", Input).value = str(out)
        app.screen.query_one("#export-format", Select).value = "json"
        app.screen.query_one("#export-inline").value = True
        await pilot.click("#export-submit")
        await pilot.pause()
        err = app.screen.query_one("#export-error").content
        assert "cannot read document" in str(err)
        assert not out.exists()
        app.screen.query_one("#export-inline").value = False
        await press(app, pilot, "#export-submit")
        await pilot.pause()

        assert out.read_text().lstrip().startswith("{")
        assert '"name": "h"' in out.read_text()
        assert not app.screen.query("#export-path")  # modal closed


async def test_import_via_tui_reports_portable_error(tmp_path):
    db = make_db(tmp_path)
    db.save_harness(make_harness("h"))
    bad = tmp_path / "bad.yaml"
    bad.write_text("format_version: 99\nname: x\n")
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test() as pilot:
        await focus_harnesses(app, pilot)
        await open_modal(app, pilot, "i", "#import-path")
        app.screen.query_one("#import-path", Input).value = str(bad)
        await pilot.click("#import-submit")
        await pilot.pause()
        assert "unsupported format_version" in str(app.screen.query_one("#import-error").content)
        app.screen.query_one("#import-path", Input).value = str(tmp_path / "nope.yaml")
        await press(app, pilot, "#import-submit")
        await pilot.pause()
        assert "cannot read" in str(app.screen.query_one("#import-error").content).lower()
        assert [x.name for x in db.list_harnesses()] == ["h"]
        assert app.is_running


async def test_import_new_and_existing_name_with_overwrite_confirm(tmp_path):
    from promptharness.core.portable import export_harness

    db = make_db(tmp_path)
    db.save_harness(make_harness("h"))
    text = export_harness(db, "h")
    path = tmp_path / "h.yaml"
    path.write_text(text.replace("description: demo", "description: changed"))
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test() as pilot:
        t = await focus_harnesses(app, pilot)
        await open_modal(app, pilot, "i", "#import-path")
        app.screen.query_one("#import-path", Input).value = str(path)
        await pilot.click("#import-submit")
        await pilot.pause()
        assert "exists" in str(app.screen.query_one("#confirm-yes").screen.query_one("Label")
                                .content)
        await pilot.press("n")
        await pilot.pause()
        assert db.get_harness("h").description == "demo"
        await press(app, pilot, "#import-submit")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert db.get_harness("h").description == "changed"
        assert not app.screen.query("#import-path")
        path2 = tmp_path / "n.yaml"
        path2.write_text(text.replace("name: h", "name: fresh"))
        await open_modal(app, pilot, "i", "#import-path")
        app.screen.query_one("#import-path", Input).value = str(path2)
        await pilot.press("enter")
        await pilot.pause()
        assert db.get_harness("fresh") is not None
        assert t.row_count == 2


async def test_typing_in_modal_inputs_does_not_trigger_pane_or_app_keys(tmp_path):
    db = make_db(tmp_path)
    db.save_harness(make_harness("h"))
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test() as pilot:
        await focus_harnesses(app, pilot)
        for key, field in (("i", "#import-path"), ("e", "#export-path"),
                           ("m", "#regression-manual")):
            await open_modal(app, pilot, key, field)
            inp = app.screen.query_one(field, Input)
            inp.value = ""
            inp.focus()
            await pilot.press("q", "2", "m", "d", "D", "i", "e")
            await pilot.pause()
            assert app.is_running
            assert inp.value == "q2mdDie", field
            assert app.query_one(TabbedContent).active == "harnesses"
            assert [x.name for x in db.list_harnesses()] == ["h"]
            await pilot.press("escape")
            await until(pilot, lambda: not app.screen.query(field))


# ---------------------------------------------------------------- matrix


async def test_regression_matrix_fills_cells_for_two_models(tmp_path):
    db = make_db(tmp_path)
    db.save_harness(make_harness("h"))
    fn = by_model({"m1": {"one": "ok 1", "two": "ok 2"},
                   "m2": {"one": "ok!", "two": "nope"}})
    app = PromptHarnessApp(db=db, client=FakeClient([fn] * 4))
    async with app.run_test() as pilot:
        await start_regression(app, pilot, ["p:m1", "p:m2"])
        screen = app.screen
        assert isinstance(screen, MatrixScreen)
        assert cell(screen, "c1", "p:m1") == "pass"
        assert cell(screen, "c2", "p:m1") == "pass"
        assert cell(screen, "c1", "p:m2") == "pass"
        assert cell(screen, "c2", "p:m2") == "fail"
        runs = db.list_runs("h")
        assert sorted(str(r.model) for r in runs) == ["p:m1", "p:m2"]
        assert all(r.id is not None and len(r.results) == 2 for r in runs)


async def test_regression_manual_model_entry_and_validation(tmp_path):
    db = make_db(tmp_path)
    db.save_harness(make_harness("h"))
    fn = by_model({"x9": {"one": "ok", "two": "ok"}})
    app = PromptHarnessApp(db=db, client=FakeClient([fn] * 2))
    async with app.run_test() as pilot:
        await focus_harnesses(app, pilot)
        await open_modal(app, pilot, "m", "#regression-models")
        await pilot.click("#regression-run")
        await pilot.pause()
        assert "select at least one" in str(
            app.screen.query_one("#regression-error").content).lower()
        app.screen.query_one("#regression-manual", Input).value = "garbage"
        await press(app, pilot, "#regression-run")
        await pilot.pause()
        assert "provider:model" in str(app.screen.query_one("#regression-error").content)
        app.screen.query_one("#regression-manual", Input).value = "p:x9"
        await press(app, pilot, "#regression-run")
        await settle(app, pilot)
        assert isinstance(app.screen, MatrixScreen)
        assert cell(app.screen, "c1", "p:x9") == "pass"


async def test_regression_disabled_provider_not_offered(tmp_path):
    db = make_db(tmp_path)
    db.save_harness(make_harness("h"))
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test() as pilot:
        await focus_harnesses(app, pilot)
        await open_modal(app, pilot, "m", "#regression-models")
        sl = app.screen.query_one("#regression-models", SelectionList)
        values = [sl.get_option_at_index(i).value for i in range(sl.option_count)]
        assert values == ["p:m1", "p:m2"]


async def test_judge_same_as_tested_model_warns(tmp_path):
    db = make_db(tmp_path)
    db.save_harness(make_harness("h"))
    fn = by_model({"m1": {"one": "ok", "two": "ok"}})
    app = PromptHarnessApp(db=db, client=FakeClient([fn] * 2))
    async with app.run_test() as pilot:
        await start_regression(app, pilot, ["p:m1"], judge="p:m1")
        assert any("judge" in n.message.lower() and "same" in n.message.lower()
                   for n in app._notifications)
        assert db.list_runs("h")[0].judge_model == ModelRef.parse("p:m1")


async def test_matrix_error_cell_shown_and_app_survives(tmp_path):
    db = make_db(tmp_path)
    db.save_harness(make_harness("h"))
    fn = by_model({"m1": {"one": "ok", "two": ClientError("auth", "bad key")},
                   "m2": {"one": RuntimeError("kaboom"), "two": "ok"}})
    app = PromptHarnessApp(db=db, client=FakeClient([fn] * 4))
    async with app.run_test() as pilot:
        await start_regression(app, pilot, ["p:m1", "p:m2", "nosuch:z"])
        screen = app.screen
        assert app.is_running
        assert cell(screen, "c1", "p:m1") == "pass"
        assert cell(screen, "c2", "p:m1") == "error"
        assert cell(screen, "c1", "p:m2") == "error"
        assert cell(screen, "c1", "nosuch:z") == "error"
        assert cell(screen, "c2", "nosuch:z") == "error"
        screen.open_detail("c2", "p:m1")
        await pilot.pause()
        assert "bad key" in app.screen.text


async def test_matrix_target_whose_run_raises_shows_errors(tmp_path, monkeypatch):
    from promptharness.tui import matrix

    real = matrix.run_harness

    async def flaky(harness, target, *a, **kw):
        if target.model == "m1":
            raise RuntimeError("engine blew up")
        return await real(harness, target, *a, **kw)

    monkeypatch.setattr(matrix, "run_harness", flaky)
    db = make_db(tmp_path)
    db.save_harness(make_harness("h"))
    fn = by_model({"m2": {"one": "ok", "two": "ok"}})
    app = PromptHarnessApp(db=db, client=FakeClient([fn] * 2))
    async with app.run_test() as pilot:
        await start_regression(app, pilot, ["p:m1", "p:m2"])
        screen = app.screen
        assert cell(screen, "c1", "p:m1") == "error"
        assert cell(screen, "c2", "p:m1") == "error"
        assert cell(screen, "c1", "p:m2") == "pass"
        assert not screen.running
        screen.open_detail("c1", "p:m1")
        await pilot.pause()
        assert "engine blew up" in app.screen.text


async def test_matrix_detail_view_shows_check_reasons(tmp_path):
    db = make_db(tmp_path)
    h = make_harness("h")
    db.save_harness(h)
    fn = by_model({"m1": {"one": "nothing here", "two": "ok"}})
    app = PromptHarnessApp(db=db, client=FakeClient([fn] * 2))
    async with app.run_test() as pilot:
        app.push_screen(MatrixScreen(h, [ModelRef.parse("p:m1")]))
        await settle(app, pilot)
        matrix = app.screen
        assert cell(matrix, "c1", "p:m1") == "fail"
        t = matrix.query_one("#matrix", DataTable)
        t.focus()
        t.move_cursor(row=0, column=1)
        await pilot.press("enter")
        await pilot.pause()
        detail = app.screen
        assert isinstance(detail, ResultDetail)
        assert "pattern not found: 'ok'" in detail.text
        assert "nothing here" in detail.text
        assert "p:m1" in detail.text
        # manual verdict: override to pass, persisted
        await pilot.press("v")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert "manual verdict: pass" in app.screen.text
        run = db.list_runs("h")[0]
        r1 = next(r for r in run.results if r.case_name == "c1")
        assert r1.manual_verdict is True
        assert r1.status == "fail"  # failing check still wins (core/status)
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is matrix
        assert cell(matrix, "c1", "p:m1") == "fail"


async def test_matrix_manual_case_verdict_changes_cell(tmp_path):
    db = make_db(tmp_path)
    h = Harness(name="h", prompt=PromptVersion(template="{{ input }}"),
                cases=[Case(name="c1", input="one")])
    db.save_harness(h)
    app = PromptHarnessApp(db=db, client=FakeClient(["whatever"]))
    async with app.run_test() as pilot:
        app.push_screen(MatrixScreen(h, [ModelRef.parse("p:m1")]))
        await settle(app, pilot)
        matrix = app.screen
        assert cell(matrix, "c1", "p:m1") == "manual"
        matrix.open_detail("c1", "p:m1")
        await pilot.pause()
        await pilot.press("v", "y")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert cell(matrix, "c1", "p:m1") == "pass"
        assert db.list_runs("h")[0].results[0].status == "pass"


async def test_matrix_cancel_midway_saves_finished_models_and_survives(tmp_path):
    db = make_db(tmp_path)
    db.save_harness(make_harness("h"))
    gate = asyncio.Event()

    async def fn(provider, model, messages, params):
        if model == "m2":
            await gate.wait()
        return "ok"

    app = PromptHarnessApp(db=db, client=FakeClient([fn] * 4))
    async with app.run_test() as pilot:
        await focus_harnesses(app, pilot)
        await open_modal(app, pilot, "m", "#regression-models")
        sl = app.screen.query_one("#regression-models", SelectionList)
        sl.select("p:m1")
        sl.select("p:m2")
        await pilot.click("#regression-run")
        for _ in range(20):
            await pilot.pause()
            if len(db.list_runs("h")) == 1:
                break
        matrix = app.screen
        assert isinstance(matrix, MatrixScreen)
        assert matrix.running
        assert cell(matrix, "c1", "p:m2") == "…"
        await pilot.press("escape")
        await pilot.pause()
        gate.set()
        await settle(app, pilot)
        assert app.is_running
        assert not isinstance(app.screen, MatrixScreen)
        assert [str(r.model) for r in db.list_runs("h")] == ["p:m1"]


# ---------------------------------------------------------------- runs


async def test_runs_pane_lists_runs_after_regression(tmp_path):
    db = make_db(tmp_path)
    db.save_harness(make_harness("h"))
    fn = by_model({"m1": {"one": "ok", "two": "nope"},
                   "m2": {"one": "ok", "two": "ok"}})
    app = PromptHarnessApp(db=db, client=FakeClient([fn] * 4))
    async with app.run_test() as pilot:
        await pilot.press("3")
        await pilot.pause()
        runs_table = app.query_one("#runs-table", DataTable)
        assert runs_table.row_count == 0
        await start_regression(app, pilot, ["p:m1", "p:m2"])
        await pilot.press("escape")
        await until(pilot, lambda: not isinstance(app.screen, MatrixScreen))
        await pilot.press("3")
        await pilot.pause()
        assert runs_table.row_count == 2
        assert app.focused is runs_table
        rows = [[str(c) for c in runs_table.get_row_at(i)] for i in range(2)]
        by_model_row = {r[3]: r for r in rows}
        # columns: mark, time, harness, model, pass, fail, manual, error, judge_error
        assert by_model_row["p:m1"][2] == "h"
        assert by_model_row["p:m1"][4:6] == ["1", "1"]
        assert by_model_row["p:m2"][4:6] == ["2", "0"]
        # Enter opens the run read-only
        await pilot.press("enter")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MatrixScreen)
        assert screen.read_only
        assert screen.query_one("#matrix", DataTable).row_count == 2
        screen.open_detail("c1", screen.column_keys[0])
        await pilot.pause()
        await pilot.press("v")
        await pilot.pause()
        assert isinstance(app.screen, ResultDetail)  # verdict disabled read-only
        await pilot.press("escape", "escape")
        await pilot.pause()
        assert app.query_one(TabbedContent).active == "runs"


async def test_runs_pane_marked_runs_open_side_by_side(tmp_path):
    db = make_db(tmp_path)
    db.save_harness(make_harness("h"))
    fn = by_model({"m1": {"one": "ok", "two": "nope"},
                   "m2": {"one": "ok", "two": "ok"}})
    app = PromptHarnessApp(db=db, client=FakeClient([fn] * 4))
    async with app.run_test() as pilot:
        await start_regression(app, pilot, ["p:m1", "p:m2"])
        await pilot.press("escape")
        await until(pilot, lambda: not isinstance(app.screen, MatrixScreen))
        await pilot.press("3")
        await pilot.pause()
        t = app.query_one("#runs-table", DataTable)
        assert app.focused is t
        t.move_cursor(row=0)
        await pilot.press("space", "space")  # space marks and moves down
        await pilot.pause()
        assert t.cursor_coordinate.row == 1
        await pilot.press("enter")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MatrixScreen)
        assert len(screen.column_keys) == 2
        runs = {str(r.model): r for r in db.list_runs("h")}
        k1 = next(k for k in screen.column_keys if k.endswith(f"#{runs['p:m1'].id}"))
        k2 = next(k for k in screen.column_keys if k.endswith(f"#{runs['p:m2'].id}"))
        assert cell(screen, "c2", k1) == "fail"
        assert cell(screen, "c2", k2) == "pass"


async def test_number_keys_do_nothing_while_matrix_is_open(tmp_path):
    db = make_db(tmp_path)
    db.save_harness(make_harness("h"))
    fn = by_model({"m1": {"one": "ok", "two": "ok"}})
    app = PromptHarnessApp(db=db, client=FakeClient([fn] * 2))
    async with app.run_test() as pilot:
        await start_regression(app, pilot, ["p:m1"])
        matrix = app.screen
        await pilot.press("3", "4")
        await pilot.pause()
        assert app.screen is matrix
        assert app.query_one(TabbedContent).active == "harnesses"
        matrix.open_detail("c1", "p:m1")
        await pilot.pause()
        await pilot.press("2")
        await pilot.pause()
        assert isinstance(app.screen, ResultDetail)
        assert app.query_one(TabbedContent).active == "harnesses"


async def test_fast_tab_switch_from_harnesses_does_not_bounce(tmp_path):
    app = PromptHarnessApp(db=make_db(tmp_path), client=FakeClient([]))
    async with app.run_test() as pilot:
        await pilot.press("2", "1", "3", "1", "2")
        await pilot.pause()
        await pilot.pause()
        assert app.query_one(TabbedContent).active == "studio"
