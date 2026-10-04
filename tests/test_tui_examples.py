"""The TUI import box accepts example:NAME for the harnesses bundled with the package."""

from __future__ import annotations

from textual.widgets import Button, Input, Label

from promptharness.core.db import Database
from promptharness.tui.app import PromptHarnessApp


async def _wait(pilot, predicate, tries: int = 100) -> None:
    for _ in range(tries):
        if predicate():
            return
        await pilot.pause()
    raise AssertionError("condition not reached")


async def _open_import(app, pilot):
    await pilot.press("1")
    await pilot.pause()
    await pilot.press("i")
    await _wait(pilot, lambda: bool(app.screen.query("#import-path")))


async def test_import_box_accepts_a_bundled_example(tmp_path):
    db = Database(tmp_path / "t.db")
    app = PromptHarnessApp(db=db)
    async with app.run_test(size=(120, 30)) as pilot:
        await _open_import(app, pilot)
        app.screen.query_one("#import-path", Input).value = "example:quickstart"
        app.screen.query_one("#import-submit", Button).press()
        await _wait(pilot, lambda: not app.screen.query("#import-path"))
        assert [h.name for h in db.list_harnesses()] == ["quickstart"]


async def test_import_box_unknown_example_shows_the_choices(tmp_path):
    db = Database(tmp_path / "t.db")
    app = PromptHarnessApp(db=db)
    async with app.run_test(size=(120, 30)) as pilot:
        await _open_import(app, pilot)
        app.screen.query_one("#import-path", Input).value = "example:nope"
        app.screen.query_one("#import-submit", Button).press()
        await pilot.pause()
        msg = str(app.screen.query_one("#import-error", Label).render())
        assert "nope" in msg and "quickstart" in msg
        assert app.screen.query("#import-path")  # dialog stays open
        assert db.list_harnesses() == []


async def test_import_box_still_reads_file_paths(tmp_path):
    from promptharness.core.examples import read_example

    f = tmp_path / "mine.yaml"
    f.write_text(read_example("summarize"), encoding="utf-8")
    db = Database(tmp_path / "t.db")
    app = PromptHarnessApp(db=db)
    async with app.run_test(size=(120, 30)) as pilot:
        await _open_import(app, pilot)
        app.screen.query_one("#import-path", Input).value = str(f)
        app.screen.query_one("#import-submit", Button).press()
        await _wait(pilot, lambda: not app.screen.query("#import-path"))
        assert [h.name for h in db.list_harnesses()] == ["summarize-example"]
