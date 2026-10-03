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


async def _fill(app, name, url, env):
    app.screen.query_one("#name", Input).value = name
    app.screen.query_one("#base_url", Input).value = url
    app.screen.query_one("#api_key_env", Input).value = env


async def test_add_duplicate_name_is_rejected(tmp_path):
    db = make_db(tmp_path)
    db.save_provider(Provider(name="acme", base_url="https://a.test", api_key_env="K1", enabled=False))
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test() as pilot:
        await pilot.press("4", "a")
        await pilot.pause()
        await _fill(app, "acme", "https://other.test", "K2")
        await pilot.click("#submit")
        await pilot.pause()
        assert app.screen.query("#submit")
        assert any("exists" in n.message for n in app._notifications)
        got = db.get_provider("acme")
        assert (got.base_url, got.api_key_env, got.enabled) == ("https://a.test", "K1", False)


async def test_invalid_base_url_is_rejected(tmp_path):
    db = make_db(tmp_path)
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test() as pilot:
        await pilot.press("4", "a")
        await pilot.pause()
        for bad in ("api.test/v1", "ftp://x.test", "https://", "http:///path"):
            await _fill(app, "acme", bad, "K")
            await pilot.click("#submit")
            await pilot.pause()
            assert app.screen.query("#submit"), bad
            assert db.list_providers() == [], bad
        assert any("URL" in n.message for n in app._notifications)


async def test_edit_prefills_and_preserves_fields(tmp_path):
    db = make_db(tmp_path)
    db.save_provider(Provider(name="acme", base_url="https://a.test", api_key_env="K1",
                              enabled=False, timeout=5.0, headers={"x": "y"}))
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test() as pilot:
        await pilot.press("4", "e")
        await pilot.pause()
        assert app.screen.query_one("#name", Input).value == "acme"
        assert app.screen.query_one("#name", Input).disabled
        assert app.screen.query_one("#base_url", Input).value == "https://a.test"
        assert app.screen.query_one("#api_key_env", Input).value == "K1"
        app.screen.query_one("#base_url", Input).value = "https://b.test"
        await pilot.click("#submit")
        await pilot.pause()
        got = db.get_provider("acme")
        assert got.base_url == "https://b.test"
        assert got.enabled is False and got.timeout == 5.0 and got.headers == {"x": "y"}
        assert len(db.list_providers()) == 1


async def test_cancel_leaves_db_unchanged(tmp_path):
    db = make_db(tmp_path)
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test() as pilot:
        await pilot.press("4", "a")
        await pilot.pause()
        await _fill(app, "acme", "https://a.test", "K")
        await pilot.click("#cancel")
        await pilot.pause()
        assert not app.screen.query("#submit")
        assert db.list_providers() == []


async def test_typing_in_input_does_not_trigger_app_keys(tmp_path):
    app = PromptHarnessApp(db=make_db(tmp_path), client=FakeClient([]))
    async with app.run_test() as pilot:
        await pilot.press("4", "a")
        await pilot.pause()
        app.screen.query_one("#name", Input).focus()
        await pilot.press("q", "2", "3")
        await pilot.pause()
        assert app.is_running
        assert app.query_one(TabbedContent).active == "providers"
        assert app.screen.query_one("#name", Input).value == "q23"


async def test_landing_on_providers_focuses_table(tmp_path):
    app = PromptHarnessApp(db=make_db(tmp_path), client=FakeClient([]))
    async with app.run_test() as pilot:
        await pilot.press("2", "4")
        await pilot.pause()
        assert app.focused is app.query_one("#providers-table")
        await pilot.press("1", "4")
        await pilot.pause()
        assert app.focused is app.query_one("#providers-table")
