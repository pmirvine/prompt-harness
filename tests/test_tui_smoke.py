from __future__ import annotations

from conftest import FakeClient
from textual.widgets import DataTable, Input, TabbedContent

from promptharness.core.client import ClientError
from promptharness.core.db import Database
from promptharness.core.models import Provider
from promptharness.tui.app import PromptHarnessApp


def make_db(tmp_path) -> Database:
    return Database(tmp_path / "tui.db")


async def test_app_starts_and_tabs_switch(tmp_path):
    app = PromptHarnessApp(db=make_db(tmp_path), client=FakeClient([]))
    async with app.run_test() as pilot:
        tabs = app.query_one(TabbedContent)
        assert tabs.active == "harnesses"
        await pilot.press("2")
        assert tabs.active == "studio"
        await pilot.press("3")
        assert tabs.active == "runs"
        await pilot.press("4")
        assert tabs.active == "providers"
        await pilot.press("1")
        assert tabs.active == "harnesses"


async def test_q_quits(tmp_path):
    app = PromptHarnessApp(db=make_db(tmp_path), client=FakeClient([]))
    async with app.run_test() as pilot:
        await pilot.press("q")
        await pilot.pause()
    assert not app.is_running


async def test_add_provider_via_form(tmp_path):
    db = make_db(tmp_path)
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test() as pilot:
        await pilot.press("4")
        await pilot.press("a")
        await pilot.pause()
        app.screen.query_one("#name", Input).value = "acme"
        app.screen.query_one("#base_url", Input).value = "https://x.test/v1"
        app.screen.query_one("#api_key_env", Input).value = "ACME_KEY"
        await pilot.click("#submit")
        await pilot.pause()
        got = db.get_provider("acme")
        assert got is not None
        assert got.base_url == "https://x.test/v1"
        assert got.api_key_env == "ACME_KEY"
        assert app.query_one("#providers-table", DataTable).row_count == 1


async def test_form_rejects_empty_fields(tmp_path):
    db = make_db(tmp_path)
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test() as pilot:
        await pilot.press("4", "a")
        await pilot.pause()
        await pilot.click("#submit")
        await pilot.pause()
        assert db.list_providers() == []
        assert app.screen.query("#submit")  # form still open


async def test_toggle_enabled(tmp_path):
    db = make_db(tmp_path)
    db.save_provider(Provider(name="p", base_url="u", api_key_env="K"))
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test() as pilot:
        await pilot.press("4")
        app.query_one("#providers-table", DataTable).focus()
        await pilot.press("d")
        await pilot.pause()
        assert db.get_provider("p").enabled is False


async def test_connection_success_saves_models(tmp_path):
    db = make_db(tmp_path)
    db.save_provider(Provider(name="p", base_url="u", api_key_env="K"))
    app = PromptHarnessApp(db=db, client=FakeClient([], models=["m1", "m2"]))
    async with app.run_test() as pilot:
        await pilot.press("4")
        app.query_one("#providers-table", DataTable).focus()
        await pilot.press("t")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert db.list_models("p") == ["m1", "m2"]
        assert any("2" in n.message for n in app._notifications)


async def test_connection_failure_does_not_crash(tmp_path):
    db = make_db(tmp_path)
    db.save_provider(Provider(name="p", base_url="u", api_key_env="K"))
    client = FakeClient([], models=ClientError("auth", "bad key"))
    app = PromptHarnessApp(db=db, client=client)
    async with app.run_test() as pilot:
        await pilot.press("4")
        app.query_one("#providers-table", DataTable).focus()
        await pilot.press("t")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.is_running
        assert any("bad key" in n.message for n in app._notifications)
        # manual entry is offered
        assert app.screen.query("#models")


async def test_unexpected_exception_does_not_crash(tmp_path):
    db = make_db(tmp_path)
    db.save_provider(Provider(name="p", base_url="u", api_key_env="K"))
    app = PromptHarnessApp(db=db, client=FakeClient([], models=RuntimeError("boom")))
    async with app.run_test() as pilot:
        await pilot.press("4")
        app.query_one("#providers-table", DataTable).focus()
        await pilot.press("t")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.is_running


async def test_manual_model_entry(tmp_path):
    db = make_db(tmp_path)
    db.save_provider(Provider(name="p", base_url="u", api_key_env="K"))
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test() as pilot:
        await pilot.press("4")
        app.query_one("#providers-table", DataTable).focus()
        await pilot.press("m")
        await pilot.pause()
        app.screen.query_one("#models").text = "gpt-a\ngpt-b"
        await pilot.click("#submit")
        await pilot.pause()
        assert db.list_models("p") == ["gpt-a", "gpt-b"]
