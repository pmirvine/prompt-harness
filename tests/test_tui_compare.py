"""TUI tests: Compare screen, read-only verdicts and re-test on replacement."""

from __future__ import annotations

from conftest import FakeClient
from test_tui_harnesses import by_model, cell, make_db, make_harness, press, settle, until
from textual.widgets import DataTable, Input, Select, TabbedContent

from promptharness.core.models import CaseResult, ModelRef, PromptVersion, Run
from promptharness.tui.app import PromptHarnessApp
from promptharness.tui.compare import CompareScreen
from promptharness.tui.matrix import MatrixScreen, ResultDetail
from promptharness.tui.retest_modal import RetestForm


def result(case: str, output: str, status: str = "pass", **kw) -> CaseResult:
    return CaseResult(case_name=case, status=status, output=output, **kw)


def save_run(db, harness: str, model: str, outputs: dict[str, str],
             prompt_hash: str | None = None) -> Run:
    h = db.get_harness(harness)
    run = Run(harness=harness, prompt_hash=prompt_hash or (h.prompt.hash if h else "x"),
              model=ModelRef.parse(model), started_at="2026-10-03T00:00:00+00:00",
              finished_at="2026-10-03T00:00:01+00:00",
              results=[result(c, o) for c, o in outputs.items()])
    db.save_run(run)
    return run


def accept(db, harness: str, run: Run) -> None:
    db.set_accepted(harness, run.model, run.id)


def notified(app, text: str) -> bool:
    return any(text in n.message for n in app._notifications)


# ---------------------------------------------------------------- compare screen


async def test_compare_screen_shows_accepted_first_then_models(tmp_path):
    app = PromptHarnessApp(db=make_db(tmp_path), client=FakeClient([]))
    long = "golden answer " * 40
    async with app.run_test() as pilot:
        app.push_screen(CompareScreen("c1", [
            ("accepted: openai:gpt-4o", result("c1", long, latency_ms=120,
                                               prompt_tokens=10, completion_tokens=20)),
            ("groq:llama", result("c1", "new answer", status="fail")),
        ]))
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, CompareScreen)
        assert screen.labels == ["accepted: openai:gpt-4o", "groq:llama"]
        texts = screen.pane_texts
        assert "golden answer" in texts[0] and "new answer" in texts[1]
        assert "PASS" in texts[0] and "120 ms" in texts[0] and "10 in / 20 out" in texts[0]
        assert "FAIL" in texts[1]
        panes = screen.panes
        assert panes[0].size.width == panes[1].size.width  # equal-width
        assert app.focused is panes[0]
    assert app.client.calls == []


async def test_compare_handles_missing_result_column(tmp_path):
    app = PromptHarnessApp(db=make_db(tmp_path), client=FakeClient([]))
    async with app.run_test() as pilot:
        app.push_screen(CompareScreen("c1", [("p:m1", result("c1", "hello")),
                                             ("p:m2", None)]))
        await pilot.pause()
        assert "no result" in app.screen.pane_texts[1]
        assert "hello" in app.screen.pane_texts[0]
    assert app.client.calls == []


async def test_compare_left_right_move_focus_and_escape_goes_back(tmp_path):
    app = PromptHarnessApp(db=make_db(tmp_path), client=FakeClient([]))
    async with app.run_test() as pilot:
        await pilot.pause()
        base = app.screen
        app.push_screen(CompareScreen("c1", [("a", None), ("b", None), ("c", None)]))
        await pilot.pause()
        panes = app.screen.panes
        assert app.focused is panes[0]
        await pilot.press("right", "right", "right")
        assert app.focused is panes[2]
        await pilot.press("left")
        assert app.focused is panes[1]
        await pilot.press("2")  # number keys disabled on pushed screens
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is base
        assert app.query_one(TabbedContent).active == "harnesses"
    assert app.client.calls == []


# ---------------------------------------------------------------- matrix -> compare


async def run_live_matrix(app, pilot, harness, models):
    app.push_screen(MatrixScreen(harness, [ModelRef.parse(m) for m in models]))
    await settle(app, pilot)
    return app.screen


async def test_matrix_c_opens_compare_with_accepted_first(tmp_path):
    db = make_db(tmp_path)
    db.save_harness(make_harness("h"))
    golden = save_run(db, "h", "p:m1", {"c1": "golden one", "c2": "golden two"})
    accept(db, "h", golden)
    h = db.get_harness("h")
    fn = by_model({"m1": {"one": "ok m1", "two": "ok"}, "m2": {"one": "ok m2", "two": "ok"}})
    app = PromptHarnessApp(db=db, client=FakeClient([fn] * 4))
    async with app.run_test() as pilot:
        matrix = await run_live_matrix(app, pilot, h, ["p:m1", "p:m2"])
        t = matrix.query_one("#matrix", DataTable)
        t.move_cursor(row=1, column=0)  # highlighted case row c2
        await pilot.press("c")
        await until(pilot, lambda: isinstance(app.screen, CompareScreen))
        cmp = app.screen
        assert cmp.case_name == "c2"
        assert cmp.labels == ["accepted: p:m1", "p:m1", "p:m2"]
        assert "golden two" in cmp.pane_texts[0]
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is matrix


async def test_compare_accepted_column_for_duplicated_harness(tmp_path):
    db = make_db(tmp_path)
    db.save_harness(make_harness("h"))
    golden = save_run(db, "h", "p:m1", {"c1": "golden one", "c2": "golden two"})
    accept(db, "h", golden)
    dup = db.get_harness("h").model_copy(update={"name": "h copy"}, deep=True)
    db.save_harness(dup)
    fn = by_model({"m2": {"one": "ok m2", "two": "ok"}})
    app = PromptHarnessApp(db=db, client=FakeClient([fn] * 2))
    async with app.run_test() as pilot:
        matrix = await run_live_matrix(app, pilot, db.get_harness("h copy"), ["p:m2"])
        assert matrix.compare_columns("c1")[0] == ("accepted: p:m1", golden.results[0])
        assert [lbl for lbl, _ in matrix.compare_columns("c1")] == ["accepted: p:m1", "p:m2"]


async def test_compare_without_accepted_run_or_case_has_only_models(tmp_path):
    db = make_db(tmp_path)
    db.save_harness(make_harness("h"))
    golden = save_run(db, "h", "p:m1", {"c1": "golden one"})  # no c2 in accepted run
    accept(db, "h", golden)
    db.save_harness(make_harness("plain", accepted=None))
    fn = by_model({"m2": {"one": "ok", "two": "ok"}})
    app = PromptHarnessApp(db=db, client=FakeClient([fn] * 4))
    async with app.run_test() as pilot:
        matrix = await run_live_matrix(app, pilot, db.get_harness("h"), ["p:m2"])
        assert [lbl for lbl, _ in matrix.compare_columns("c2")] == ["p:m2"]
        app.pop_screen()
        matrix = await run_live_matrix(app, pilot, db.get_harness("plain"), ["p:m2"])
        assert [lbl for lbl, _ in matrix.compare_columns("c1")] == ["p:m2"]


async def test_detail_view_c_opens_compare_for_its_case(tmp_path):
    db = make_db(tmp_path)
    db.save_harness(make_harness("h"))
    fn = by_model({"m1": {"one": "ok m1", "two": "ok"}})
    app = PromptHarnessApp(db=db, client=FakeClient([fn] * 2))
    async with app.run_test() as pilot:
        matrix = await run_live_matrix(app, pilot, db.get_harness("h"), ["p:m1"])
        matrix.open_detail("c1", "p:m1")
        await until(pilot, lambda: isinstance(app.screen, ResultDetail))
        await pilot.press("c")
        await until(pilot, lambda: isinstance(app.screen, CompareScreen))
        assert app.screen.labels == ["p:m1"]
        assert "ok m1" in app.screen.pane_texts[0]


async def test_read_only_matrix_compare_columns_come_from_runs(tmp_path):
    db = make_db(tmp_path)
    db.save_harness(make_harness("h"))
    golden = save_run(db, "h", "p:m1", {"c1": "golden one", "c2": "golden two"})
    accept(db, "h", golden)
    r2 = save_run(db, "h", "p:m2", {"c1": "second one"})
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test() as pilot:
        app.push_screen(MatrixScreen.from_runs([db.get_run(r2.id)]))
        await pilot.pause()
        matrix = app.screen
        cols = matrix.compare_columns("c1")
        assert [lbl for lbl, _ in cols] == ["accepted: p:m1", f"p:m2 #{r2.id}"]
        assert cols[1][1].output == "second one"
        assert matrix.compare_columns("c2")[1][1] is None
    assert app.client.calls == []


# ---------------------------------------------------------------- read-only verdicts


async def test_verdict_on_stored_run_persists_and_updates_cell(tmp_path):
    db = make_db(tmp_path)
    db.save_harness(make_harness("h"))
    run = save_run(db, "h", "p:m1", {"c1": "whatever"})
    db.set_manual_verdict(run.results[0].id, None)  # status recomputed: manual
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test() as pilot:
        app.push_screen(MatrixScreen.from_runs([db.get_run(run.id)]))
        await pilot.pause()
        matrix = app.screen
        key = matrix.column_keys[0]
        assert cell(matrix, "c1", key) == "manual"
        matrix.open_detail("c1", key)
        await until(pilot, lambda: isinstance(app.screen, ResultDetail))
        await pilot.press("v")
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert "manual verdict: fail" in app.screen.text
        stored = db.get_run(run.id).results[0]
        assert stored.manual_verdict is False and stored.status == "fail"
        await pilot.press("escape")
        await pilot.pause()
        assert cell(matrix, "c1", key) == "fail"
    assert app.client.calls == []


async def test_read_only_verdict_without_result_id_shows_message(tmp_path):
    db = make_db(tmp_path)
    unsaved = Run(harness="h", prompt_hash="x", model=ModelRef.parse("p:m1"), id=99,
                  started_at="2026-10-03T00:00:00+00:00", results=[result("c1", "hi")])
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test() as pilot:
        app.push_screen(MatrixScreen.from_runs([unsaved]))
        await pilot.pause()
        app.screen.open_detail("c1", app.screen.column_keys[0])
        await until(pilot, lambda: isinstance(app.screen, ResultDetail))
        await pilot.press("v")
        await pilot.pause()
        assert isinstance(app.screen, ResultDetail)
        assert notified(app, "Read-only")
    assert app.client.calls == []


# ---------------------------------------------------------------- re-test


async def open_retest(app, pilot) -> DataTable:
    await pilot.press("3")
    await pilot.pause()
    t = app.query_one("#runs-table", DataTable)
    t.focus()
    t.move_cursor(row=0)
    await pilot.pause()
    await pilot.press("r")
    await until(pilot, lambda: isinstance(app.screen, RetestForm))
    return t


async def test_retest_picks_new_model_and_opens_resulting_run(tmp_path):
    db = make_db(tmp_path)
    db.save_harness(make_harness("h"))
    original = save_run(db, "h", "p:m1", {"c1": "ok", "c2": "ok"})
    fn = by_model({"m2": {"one": "ok new", "two": "bad"}})
    app = PromptHarnessApp(db=db, client=FakeClient([fn] * 2))
    async with app.run_test() as pilot:
        t = await open_retest(app, pilot)
        assert t.row_count == 1
        form = app.screen
        sel = form.query_one("#retest-model", Select)
        assert "p:m2" in [str(o) for o in form.options]
        assert "off:hidden" not in form.options  # disabled provider not offered
        # same model rejected with a message
        sel.value = "p:m1"
        await pilot.pause()
        await press(app, pilot, "#retest-run")
        assert isinstance(app.screen, RetestForm)
        assert "same model" in str(form.query_one("#retest-error").render())
        sel.value = "p:m2"
        await pilot.pause()
        await press(app, pilot, "#retest-run")
        await until(pilot, lambda: isinstance(app.screen, MatrixScreen))
        matrix = app.screen
        assert matrix.read_only
        runs = db.list_runs("h")
        assert len(runs) == 2
        new = next(r for r in runs if r.id != original.id)
        assert str(new.model) == "p:m2"
        # the original run sits beside the re-test
        assert len(matrix.column_keys) == 2
        orig_key, key = matrix.column_keys
        assert set(matrix.saved_runs) == {orig_key, key}
        assert matrix.saved_runs[orig_key].id == original.id
        assert matrix.saved_runs[key].id == new.id
        assert matrix.column_model(orig_key) == ModelRef.parse("p:m1")
        assert matrix.column_model(key) == ModelRef.parse("p:m2")
        assert cell(matrix, "c1", orig_key) == "pass"
        assert cell(matrix, "c1", key) == "pass"
        assert cell(matrix, "c2", key) == "fail"
        labels = [lbl for lbl, _ in matrix.compare_columns("c1")]
        assert f"p:m1 #{original.id}" in labels and f"p:m2 #{new.id}" in labels
        assert t.row_count == 2  # runs table refreshed
        assert not notified(app, "prompt changed")
        await pilot.press("escape")
        await pilot.pause()
        assert app.query_one(TabbedContent).active == "runs"


async def test_retest_manual_entry_unknown_provider_shows_case_errors(tmp_path):
    db = make_db(tmp_path)
    db.save_harness(make_harness("h"))
    save_run(db, "h", "p:m1", {"c1": "ok", "c2": "ok"})
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test() as pilot:
        await open_retest(app, pilot)
        inp = app.screen.query_one("#retest-manual", Input)
        inp.focus()
        inp.value = "nope"
        await pilot.press("enter")
        await pilot.pause()
        assert "provider:model" in str(app.screen.query_one("#retest-error").render())
        inp.value = "ghost:x"
        await pilot.press("enter")
        await until(pilot, lambda: isinstance(app.screen, MatrixScreen))
        await settle(app, pilot)
        matrix = app.screen
        assert len(matrix.column_keys) == 2
        key = matrix.column_keys[-1]
        assert cell(matrix, "c1", key) == "error"
        assert "ghost" in matrix.results[("c1", key)].error
        assert app.is_running
    assert app.client.calls == []


async def test_retest_notifies_when_harness_prompt_changed(tmp_path):
    db = make_db(tmp_path)
    db.save_harness(make_harness("h"))
    save_run(db, "h", "p:m1", {"c1": "ok", "c2": "ok"})
    h = db.get_harness("h")
    db.save_harness(h.model_copy(update={"prompt": PromptVersion(template="New {{ input }}")}))
    fn = by_model({"m2": {"one": "ok", "two": "ok"}})
    app = PromptHarnessApp(db=db, client=FakeClient([fn] * 2))
    async with app.run_test() as pilot:
        await open_retest(app, pilot)
        app.screen.query_one("#retest-model", Select).value = "p:m2"
        await pilot.pause()
        await press(app, pilot, "#retest-run")
        await until(pilot, lambda: isinstance(app.screen, MatrixScreen))
        assert notified(app, "harness prompt changed since the original run")


async def test_retest_deleted_harness_shows_error_and_survives(tmp_path):
    db = make_db(tmp_path)
    db.save_harness(make_harness("h"))
    save_run(db, "h", "p:m1", {"c1": "ok"})
    db.delete_harness("h")
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test() as pilot:
        t = await open_retest(app, pilot)
        app.screen.query_one("#retest-manual", Input).value = "p:m2"
        await press(app, pilot, "#retest-run")
        await settle(app, pilot)
        assert app.is_running
        assert not isinstance(app.screen, MatrixScreen)
        assert notified(app, "no longer exists")
        assert t.row_count == 1
    assert app.client.calls == []


async def test_retest_without_run_selected_warns(tmp_path):
    app = PromptHarnessApp(db=make_db(tmp_path), client=FakeClient([]))
    async with app.run_test() as pilot:
        await pilot.press("3")
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        assert not isinstance(app.screen, RetestForm)
        assert notified(app, "No run selected")
    assert app.client.calls == []


async def test_typing_in_retest_input_does_not_trigger_keys(tmp_path):
    db = make_db(tmp_path)
    db.save_harness(make_harness("h"))
    save_run(db, "h", "p:m1", {"c1": "ok"})
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test() as pilot:
        await open_retest(app, pilot)
        inp = app.screen.query_one("#retest-manual", Input)
        inp.focus()
        await pilot.press("q", "1", "r", "c", "v", "space")
        await pilot.pause()
        assert app.is_running
        assert inp.value == "q1rcv "
        assert isinstance(app.screen, RetestForm)
        assert app.query_one(TabbedContent).active == "runs"
        await pilot.press("escape")
        await until(pilot, lambda: not isinstance(app.screen, RetestForm))
        assert len(db.list_runs()) == 1
    assert app.client.calls == []
