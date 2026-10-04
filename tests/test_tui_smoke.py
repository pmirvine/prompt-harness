from __future__ import annotations

import asyncio
import os

from conftest import FakeClient
from textual.widgets import (
    DataTable,
    Input,
    Label,
    ListView,
    RichLog,
    Select,
    TabbedContent,
    TextArea,
)

from promptharness.core.client import ClientError
from promptharness.core.db import Database
from promptharness.core.models import Case, Expectation, Match, PromptVersion, Provider
from promptharness.tui.app import PromptHarnessApp
from promptharness.tui.studio import PromptHistory, StudioPane


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
    assert app.client.calls == []


async def test_q_quits(tmp_path):
    app = PromptHarnessApp(db=make_db(tmp_path), client=FakeClient([]))
    async with app.run_test() as pilot:
        await pilot.press("q")
        await pilot.pause()
    assert not app.is_running
    assert app.client.calls == []


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
    assert app.client.calls == []


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
    assert app.client.calls == []


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
    assert app.client.calls == []


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
    assert app.client.calls == []


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
    assert app.client.calls == []


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
    assert app.client.calls == []


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
    assert app.client.calls == []


async def test_add_provider_with_empty_env_var(tmp_path):
    db = make_db(tmp_path)
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test() as pilot:
        await pilot.press("4", "a")
        await pilot.pause()
        await _fill(app, "local", "http://localhost:11434/v1", "")
        await pilot.click("#submit")
        await pilot.pause()
        got = db.get_provider("local")
        assert got is not None and got.api_key_env == ""
        assert not app.screen.query("#submit")
    assert app.client.calls == []


async def test_add_provider_with_max_completion_tokens(tmp_path):
    db = make_db(tmp_path)
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test() as pilot:
        await pilot.press("4", "a")
        await pilot.pause()
        await _fill(app, "acme", "https://a.test", "K")
        sel = app.screen.query_one("#max_tokens_param", Select)
        assert sel.value == "max_tokens"
        sel.value = "max_completion_tokens"
        await pilot.click("#submit")
        await pilot.pause()
        assert db.get_provider("acme").max_tokens_param == "max_completion_tokens"
    assert app.client.calls == []


async def test_edit_prefills_max_tokens_param(tmp_path):
    db = make_db(tmp_path)
    db.save_provider(Provider(name="acme", base_url="https://a.test", api_key_env="K1",
                              max_tokens_param="max_completion_tokens"))
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test() as pilot:
        await pilot.press("4", "e")
        await pilot.pause()
        sel = app.screen.query_one("#max_tokens_param", Select)
        assert sel.value == "max_completion_tokens"
        sel.value = "max_tokens"
        await pilot.click("#submit")
        await pilot.pause()
        assert db.get_provider("acme").max_tokens_param == "max_tokens"
    assert app.client.calls == []


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
    assert app.client.calls == []


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
    assert app.client.calls == []


async def test_landing_on_providers_focuses_table(tmp_path):
    app = PromptHarnessApp(db=make_db(tmp_path), client=FakeClient([]))
    async with app.run_test() as pilot:
        await pilot.press("2", "4")
        await pilot.pause()
        assert app.focused is app.query_one("#providers-table")
        await pilot.press("1", "4")
        await pilot.pause()
        assert app.focused is app.query_one("#providers-table")
    assert app.client.calls == []


# ---------------------------------------------------------------- studio


def test_prompt_history_undo_redo():
    h = PromptHistory()
    assert h.undo() is None
    a, b, c = (PromptVersion(template=t) for t in ("a", "b", "c"))
    h.push(a)
    assert h.undo() is None  # at start
    h.push(b)
    h.push(b)  # identical hash stored once
    h.push(PromptVersion(template="b"))
    h.push(c)
    assert len(h) == 3
    assert h.undo().template == "b"
    assert h.undo().template == "a"
    assert h.undo() is None
    assert h.redo().template == "b"
    assert h.redo().template == "c"
    assert h.redo() is None
    # pushing after undo drops the redo branch
    h.undo()
    h.push(PromptVersion(template="d"))
    assert h.redo() is None
    assert h.undo().template == "b"


def studio_db(tmp_path) -> Database:
    db = make_db(tmp_path)
    db.save_provider(Provider(name="p", base_url="https://p.test", api_key_env="K"))
    db.save_models("p", ["m1", "m2"])
    db.save_provider(Provider(name="off", base_url="https://o.test", api_key_env="K",
                              enabled=False))
    db.save_models("off", ["hidden"])
    return db


async def open_studio(app, pilot, model: str | None = "p:m1") -> StudioPane:
    await pilot.press("2")
    await pilot.pause()
    pane = app.query_one(StudioPane)
    if model is not None:
        app.query_one("#model", Select).value = model
        await pilot.pause()
    return pane


async def settle(app, pilot) -> None:
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


def log_text(app) -> str:
    log = app.query_one("#output", RichLog)
    return "\n".join("".join(seg.text for seg in strip) for strip in log.lines)


async def test_studio_model_options_from_enabled_providers(tmp_path):
    app = PromptHarnessApp(db=studio_db(tmp_path), client=FakeClient([]))
    async with app.run_test() as pilot:
        await open_studio(app, pilot, model=None)
        values = [v for _, v in app.query_one("#model", Select)._options if v != Select.NULL]
        assert values == ["p:m1", "p:m2"]
        manual = app.query_one("#manual-model", Input)
        manual.focus()
        manual.value = "x:custom"
        await pilot.press("enter")
        await pilot.pause()
        assert app.query_one("#model", Select).value == "x:custom"
        manual.value = "nocolon"
        await pilot.press("enter")
        await pilot.pause()
        assert any("provider:model" in n.message for n in app._notifications)
    assert app.client.calls == []


async def test_studio_run_one_case_shows_output(tmp_path):
    client = FakeClient(["hello"])
    app = PromptHarnessApp(db=studio_db(tmp_path), client=client)
    async with app.run_test() as pilot:
        pane = await open_studio(app, pilot)
        app.query_one("#system", TextArea).text = "be nice"
        app.query_one("#template", TextArea).text = "Say: {{ input }}"
        app.query_one("#temperature", Input).value = "0.5"
        await pane.add_case(Case(name="c1", input="hi"))
        app.query_one("#cases", ListView).focus()
        await pilot.press("r")
        await settle(app, pilot)
        assert "hello" in pane.output_text
        assert "hello" in log_text(app)
        assert "c1" in pane.output_text and "MANUAL" in pane.output_text
        call = client.calls[0]
        assert call["model"] == "m1" and call["provider"].name == "p"
        assert call["messages"] == [{"role": "system", "content": "be nice"},
                                    {"role": "user", "content": "Say: hi"}]
        assert call["params"].temperature == 0.5 and call["params"].max_tokens is None


async def test_studio_run_all_reports_each_case_and_checks(tmp_path):
    client = FakeClient(["yes it is", "nope"])
    app = PromptHarnessApp(db=studio_db(tmp_path), client=client)
    async with app.run_test() as pilot:
        pane = await open_studio(app, pilot)
        exp = Expectation(must_include=[Match(pattern="yes")])
        await pane.add_case(Case(name="a", input="1", expectation=exp))
        await pane.add_case(Case(name="b", input="2", expectation=exp))
        app.query_one("#cases", ListView).focus()
        await pilot.press("R")
        await settle(app, pilot)
        assert len(client.calls) == 2
        assert pane.result_for("a").status == "pass"
        assert pane.result_for("b").status == "fail"
        assert "PASS" in pane.output_text and "FAIL" in pane.output_text
        assert "include:yes" in pane.output_text
        assert "pattern not found" in pane.output_text


async def test_studio_failure_shown_not_crash(tmp_path):
    client = FakeClient([ClientError("timeout", "request timed out")])
    app = PromptHarnessApp(db=studio_db(tmp_path), client=client)
    async with app.run_test() as pilot:
        pane = await open_studio(app, pilot)
        await pane.add_case(Case(name="c1", input="hi"))
        app.query_one("#cases", ListView).focus()
        await pilot.press("r")
        await settle(app, pilot)
        assert app.is_running
        assert "timeout" in pane.output_text
        assert pane.result_for("c1").status == "error"


async def test_studio_template_and_provider_errors_shown(tmp_path):
    app = PromptHarnessApp(db=studio_db(tmp_path), client=FakeClient([]))
    async with app.run_test() as pilot:
        pane = await open_studio(app, pilot)
        app.query_one("#template", TextArea).text = "{{ nope }}"
        await pane.add_case(Case(name="c1", input="hi"))
        await pane.add_case(Case(name="c2", documents=[str(tmp_path / "missing.txt")]))
        app.query_one("#cases", ListView).focus()
        await pilot.press("R")
        await settle(app, pilot)
        assert "nope" in pane.output_text and "unsupported" in pane.output_text
        manual = app.query_one("#manual-model", Input)  # manual pick of disabled provider
        manual.focus()
        manual.value = "off:hidden"
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#cases", ListView).focus()
        await pilot.press("r")
        await settle(app, pilot)
        assert "disabled" in pane.output_text
        assert app.is_running
    assert app.client.calls == []


async def test_studio_run_without_model_or_cases_is_reported(tmp_path):
    app = PromptHarnessApp(db=studio_db(tmp_path), client=FakeClient([]))
    async with app.run_test() as pilot:
        pane = await open_studio(app, pilot, model=None)
        app.query_one("#cases", ListView).focus()
        await pilot.press("R")
        await settle(app, pilot)
        await pane.add_case(Case(name="c1"))
        await pilot.press("r")
        await settle(app, pilot)
        assert any("model" in n.message.lower() for n in app._notifications)
        app.query_one("#model", Select).value = "p:m1"
        app.query_one("#max_tokens", Input).value = "lots"
        await pilot.press("r")
        await settle(app, pilot)
        assert any("max tokens" in n.message.lower() for n in app._notifications)
        assert app.is_running
    assert app.client.calls == []


async def test_studio_case_new_edit_delete_via_keys(tmp_path):
    app = PromptHarnessApp(db=studio_db(tmp_path), client=FakeClient([]))
    async with app.run_test() as pilot:
        pane = await open_studio(app, pilot)
        lv = app.query_one("#cases", ListView)
        lv.focus()
        await pilot.press("n")
        await pilot.pause()
        s = app.screen
        s.query_one("#case-name", Input).value = "first"
        s.query_one("#case-input", TextArea).text = "the input"
        s.query_one("#case-docs", Input).value = "a.txt, b.txt"
        s.query_one("#must-include", TextArea).text = "foo\nba+r"
        s.query_one("#must-not-include", TextArea).text = "bad"
        s.query_one("#regex").value = True
        s.query_one("#match-mode", Select).value = "normalized"
        s.query_one("#match-text", TextArea).text = "Hello  World"
        s.query_one("#json-output").value = True
        s.query_one("#judge-prompt", TextArea).text = "is it polite?"
        s.query_one("#case-submit").press()
        await pilot.pause()
        assert [c.name for c in pane.cases] == ["first"]
        c = pane.cases[0]
        assert c.input == "the input"
        assert c.documents == [os.path.abspath("a.txt"), os.path.abspath("b.txt")]
        e = c.expectation
        assert [(m.pattern, m.regex) for m in e.must_include] == [("foo", True), ("ba+r", True)]
        assert [m.pattern for m in e.must_not_include] == ["bad"]
        assert e.normalized == "Hello  World" and e.exact is None
        assert e.json_output is True and e.judge_prompt == "is it polite?"

        # duplicate name rejected
        lv.focus()
        await pilot.press("n")
        await pilot.pause()
        app.screen.query_one("#case-name", Input).value = "first"
        app.screen.query_one("#case-submit").press()
        await pilot.pause()
        assert app.screen.query("#case-submit")
        assert any("exists" in n.message for n in app._notifications)
        app.screen.query_one("#case-cancel").press()
        await pilot.pause()

        # edit via Enter, prefilled
        lv.focus()
        lv.index = 0
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.query_one("#case-name", Input).value == "first"
        assert app.screen.query_one("#case-docs", Input).value == (
            f"{os.path.abspath('a.txt')}, {os.path.abspath('b.txt')}")
        assert app.screen.query_one("#match-mode", Select).value == "normalized"
        app.screen.query_one("#case-name", Input).value = "renamed"
        app.screen.query_one("#case-submit").press()
        await pilot.pause()
        assert [c.name for c in pane.cases] == ["renamed"]
        assert pane.cases[0].expectation.judge_prompt == "is it polite?"

        lv.focus()
        await pilot.press("x")
        await pilot.pause()
        assert pane.cases == []
        assert len(lv.children) == 0
    assert app.client.calls == []


async def test_studio_typing_in_editors_does_not_trigger_actions(tmp_path):
    client = FakeClient([])
    app = PromptHarnessApp(db=studio_db(tmp_path), client=client)
    async with app.run_test() as pilot:
        pane = await open_studio(app, pilot)
        await pane.add_case(Case(name="c1"))
        ta = app.query_one("#template", TextArea)
        ta.text = ""
        ta.focus()
        await pilot.press("n", "x", "r", "R", "s", "v", "q", "2", "4")
        await settle(app, pilot)
        assert ta.text == "nxrRsvq24"
        assert app.is_running
        assert app.query_one(TabbedContent).active == "studio"
        assert client.calls == []
        assert len(pane.cases) == 1
        assert not app.screen.query("#case-submit") and not app.screen.query("#save-name")
        sysarea = app.query_one("#system", TextArea)
        sysarea.focus()
        await pilot.press("r", "s")
        await settle(app, pilot)
        assert sysarea.text.endswith("rs")
        assert client.calls == []


async def test_studio_prompt_history_keys(tmp_path):
    app = PromptHarnessApp(db=studio_db(tmp_path), client=FakeClient(["o1", "o2"]))
    async with app.run_test() as pilot:
        pane = await open_studio(app, pilot)
        await pane.add_case(Case(name="c1"))
        ta = app.query_one("#template", TextArea)
        lv = app.query_one("#cases", ListView)
        ta.text = "v1 {{ input }}"
        lv.focus()
        await pilot.press("r")
        await settle(app, pilot)
        ta.text = "v2 {{ input }}"
        app.query_one("#temperature", Input).value = "0.7"
        lv.focus()
        await pilot.press("r")
        await settle(app, pilot)
        await pilot.press("ctrl+z")
        await pilot.pause()
        assert ta.text == "v1 {{ input }}"
        assert app.query_one("#temperature", Input).value == ""
        await pilot.press("ctrl+y")
        await pilot.pause()
        assert ta.text == "v2 {{ input }}"
        assert app.query_one("#temperature", Input).value == "0.7"


async def test_studio_edit_pause_snapshots_history(tmp_path):
    app = PromptHarnessApp(db=studio_db(tmp_path), client=FakeClient([]))
    async with app.run_test() as pilot:
        pane = await open_studio(app, pilot)
        pane.HISTORY_PAUSE = 0.05
        ta = app.query_one("#template", TextArea)
        ta.focus()

        async def wait_history(n):
            for _ in range(200):  # bounded: up to ~4s
                if len(pane.history) >= n:
                    return
                await pilot.pause(0.02)
            raise AssertionError(f"history never reached {n}: {len(pane.history)}")

        start = len(pane.history)
        await pilot.press("A")
        await wait_history(start + 1)
        after_a = ta.text
        await pilot.press("B")
        await wait_history(start + 2)
        assert ta.text != after_a
        app.query_one("#cases", ListView).focus()
        await pilot.press("ctrl+z")
        await pilot.pause()
        assert ta.text == after_a
    assert app.client.calls == []


async def test_judge_same_as_model_warns(tmp_path):
    app = PromptHarnessApp(db=studio_db(tmp_path), client=FakeClient([]))
    async with app.run_test() as pilot:
        await open_studio(app, pilot)
        judge = app.query_one("#judge", Select)
        assert judge.value == Select.NULL
        judge.value = "p:m1"
        await pilot.pause()
        assert any("judge" in n.message.lower() and n.severity == "warning"
                   for n in app._notifications)
    assert app.client.calls == []


async def _run_and_save(app, pilot, pane, name):
    app.query_one("#cases", ListView).focus()
    await pilot.press("R")
    await settle(app, pilot)
    await pilot.press("s")
    await pilot.pause()
    app.screen.query_one("#save-name", Input).value = name
    app.screen.query_one("#save-description", Input).value = "desc"
    app.screen.query_one("#save-submit").press()
    await pilot.pause()


async def test_save_as_harness_persists_harness_and_accepted_run(tmp_path):
    db = studio_db(tmp_path)
    app = PromptHarnessApp(db=db, client=FakeClient(["out-a", "out-b"]))
    async with app.run_test() as pilot:
        pane = await open_studio(app, pilot)
        app.query_one("#template", TextArea).text = "T {{ input }}"
        await pane.add_case(Case(name="a", input="1"))
        await pane.add_case(Case(name="b", input="2"))
        await _run_and_save(app, pilot, pane, "my-harness")
        h = db.get_harness("my-harness")
        assert h is not None
        assert h.description == "desc"
        assert h.prompt.template == "T {{ input }}"
        assert [c.name for c in h.cases] == ["a", "b"]
        assert str(h.accepted_model) == "p:m1"
        assert h.accepted_run_id is not None
        run = db.get_run(h.accepted_run_id)
        assert run.harness == "my-harness" and run.prompt_hash == h.prompt.hash
        assert sorted(r.output for r in run.results) == ["out-a", "out-b"]
        assert [r.case_name for r in run.results] == ["a", "b"]


async def test_save_existing_name_asks_to_overwrite(tmp_path):
    db = studio_db(tmp_path)
    app = PromptHarnessApp(db=db, client=FakeClient(["one", "two"]))
    async with app.run_test() as pilot:
        pane = await open_studio(app, pilot)
        await pane.add_case(Case(name="a"))
        await _run_and_save(app, pilot, pane, "h")
        first = db.get_harness("h").accepted_run_id
        app.query_one("#template", TextArea).text = "changed {{ input }}"
        await _run_and_save(app, pilot, pane, "h")
        assert app.screen.query("#confirm-yes")  # confirmation asked
        app.screen.query_one("#confirm-no").press()
        await pilot.pause()
        assert db.get_harness("h").accepted_run_id == first
        await pilot.press("s")
        await pilot.pause()
        assert app.screen.query_one("#save-name", Input).value == "h"
        app.screen.query_one("#save-submit").press()
        await pilot.pause()
        app.screen.query_one("#confirm-yes").press()
        await pilot.pause()
        h = db.get_harness("h")
        assert h.prompt.template == "changed {{ input }}"
        assert db.get_run(h.accepted_run_id).results[0].output == "two"


async def test_save_with_stale_results_saves_without_them(tmp_path):
    db = studio_db(tmp_path)
    app = PromptHarnessApp(db=db, client=FakeClient(["one"]))
    async with app.run_test() as pilot:
        pane = await open_studio(app, pilot)
        await pane.add_case(Case(name="a"))
        app.query_one("#cases", ListView).focus()
        await pilot.press("r")
        await settle(app, pilot)
        app.query_one("#template", TextArea).text = "edited after run {{ input }}"
        await pilot.press("s")
        await pilot.pause()
        app.screen.query_one("#save-name", Input).value = "h"
        app.screen.query_one("#save-submit").press()
        await pilot.pause()
        h = db.get_harness("h")
        assert h is not None and h.accepted_run_id is None
        assert any("no results" in n.message.lower() for n in app._notifications)


async def test_manual_verdict_key_updates_status(tmp_path):
    app = PromptHarnessApp(db=studio_db(tmp_path), client=FakeClient(["meh"]))
    async with app.run_test() as pilot:
        pane = await open_studio(app, pilot)
        await pane.add_case(Case(name="c1"))
        app.query_one("#cases", ListView).focus()
        await pilot.press("r")
        await settle(app, pilot)
        assert pane.result_for("c1").status == "manual"
        await pilot.press("v")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        r = pane.result_for("c1")
        assert r.status == "pass" and r.manual_verdict is True
        await pilot.press("v")
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert pane.result_for("c1").status == "fail"
        assert "FAIL" in pane.output_text


async def test_manual_verdict_after_save_updates_db(tmp_path):
    db = studio_db(tmp_path)
    app = PromptHarnessApp(db=db, client=FakeClient(["meh"]))
    async with app.run_test() as pilot:
        pane = await open_studio(app, pilot)
        await pane.add_case(Case(name="c1"))
        await _run_and_save(app, pilot, pane, "h")
        app.query_one("#cases", ListView).focus()
        await pilot.press("v")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        run = db.get_run(db.get_harness("h").accepted_run_id)
        assert run.results[0].manual_verdict is True
        assert run.results[0].status == "pass"
        assert pane.result_for("c1").status == "pass"


async def test_manual_verdict_without_result_warns(tmp_path):
    app = PromptHarnessApp(db=studio_db(tmp_path), client=FakeClient([]))
    async with app.run_test() as pilot:
        pane = await open_studio(app, pilot)
        await pane.add_case(Case(name="c1"))
        app.query_one("#cases", ListView).focus()
        await pilot.press("v")
        await pilot.pause()
        assert any("no result" in n.message.lower() for n in app._notifications)
    assert app.client.calls == []


async def test_leaving_studio_with_editor_focused_does_not_bounce_back(tmp_path):
    app = PromptHarnessApp(db=studio_db(tmp_path), client=FakeClient([]))
    async with app.run_test() as pilot:
        await open_studio(app, pilot)
        tabs = app.query_one(TabbedContent)
        for wid in ("#template", "#system", "#cases", "#model"):
            await pilot.press("2")
            await pilot.pause()
            app.query_one(wid).focus()
            await pilot.pause()
            tabs.active = "runs"  # e.g. a mouse click on the tab
            await pilot.pause()
            await pilot.pause()
            assert tabs.active == "runs", wid
    assert app.client.calls == []


def gated(gate: asyncio.Event, text: str):
    async def item(provider, model, messages, params):
        await gate.wait()
        return text
    return item


def toasts(app, needle: str) -> int:
    return sum(needle in n.message for n in app._notifications)


async def test_case_edited_mid_run_discards_stale_result(tmp_path):
    db = studio_db(tmp_path)
    gate = asyncio.Event()
    app = PromptHarnessApp(db=db, client=FakeClient([gated(gate, "old output")]))
    async with app.run_test() as pilot:
        pane = await open_studio(app, pilot)
        await pane.add_case(Case(name="c1", input="hi"))
        lv = app.query_one("#cases", ListView)
        lv.focus()
        await pilot.press("r")
        await pilot.pause()
        lv.index = 0
        await pilot.press("enter")
        await pilot.pause()
        app.screen.query_one("#must-include", TextArea).text = "NEW"
        app.screen.query_one("#case-submit").press()
        await pilot.pause()
        gate.set()
        await settle(app, pilot)
        assert pane.result_for("c1") is None
        assert "old output" not in pane.output_text
        assert "[ - ] c1" in str(lv.children[0].query_one("Label").render())
        lv.focus()
        await pilot.press("s")
        await pilot.pause()
        app.screen.query_one("#save-name", Input).value = "h"
        app.screen.query_one("#save-submit").press()
        await pilot.pause()
        h = db.get_harness("h")
        assert h.cases[0].expectation.must_include[0].pattern == "NEW"
        assert h.accepted_run_id is None


async def test_ctrl_r_and_ctrl_s_work_from_editors(tmp_path):
    client = FakeClient(["from editor"])
    app = PromptHarnessApp(db=studio_db(tmp_path), client=client)
    async with app.run_test() as pilot:
        pane = await open_studio(app, pilot)
        await pane.add_case(Case(name="c1"))
        ta = app.query_one("#template", TextArea)
        ta.focus()
        before = ta.text
        await pilot.press("ctrl+r")
        await settle(app, pilot)
        assert len(client.calls) == 1 and "from editor" in pane.output_text
        assert ta.text == before
        app.query_one("#temperature", Input).focus()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.screen.query("#save-name")
        hint = str(app.query_one("#studio-hint", Label).render())
        assert "Tab" in hint and "ctrl+r" in hint and "ctrl+s" in hint


async def test_alt_arrows_step_prompt_history_inside_editor(tmp_path):
    app = PromptHarnessApp(db=studio_db(tmp_path), client=FakeClient(["o1", "o2"]))
    async with app.run_test() as pilot:
        pane = await open_studio(app, pilot)
        await pane.add_case(Case(name="c1"))
        ta = app.query_one("#template", TextArea)
        ta.focus()
        ta.text = "v1 {{ input }}"
        await pilot.press("ctrl+r")
        await settle(app, pilot)
        ta.text = "v2 {{ input }}"
        await pilot.press("ctrl+r")
        await settle(app, pilot)
        ta.focus()
        bindings = app.screen.active_bindings
        assert bindings["alt+left"].binding.action == "history_undo"
        assert bindings["alt+right"].binding.action == "history_redo"
        await pilot.press("alt+left")
        await pilot.pause()
        assert ta.text == "v1 {{ input }}"
        await pilot.press("alt+right")
        await pilot.pause()
        assert ta.text == "v2 {{ input }}"
        assert "alt+" in str(app.query_one("#studio-hint", Label).render())


async def test_reentering_studio_does_not_repeat_judge_warning(tmp_path):
    app = PromptHarnessApp(db=studio_db(tmp_path), client=FakeClient([]))
    async with app.run_test() as pilot:
        await open_studio(app, pilot)
        app.query_one("#judge", Select).value = "p:m1"
        await pilot.pause()
        n = toasts(app, "Judge model is the same")
        assert n == 1
        for _ in range(3):
            await pilot.press("1")
            await pilot.pause()
            await pilot.press("2")
            await pilot.pause()
        assert toasts(app, "Judge model is the same") == n
        assert app.query_one("#judge", Select).value == "p:m1"
        assert app.query_one("#model", Select).value == "p:m1"
    assert app.client.calls == []


async def test_second_run_and_save_rejected_while_running(tmp_path):
    gate = asyncio.Event()
    client = FakeClient([gated(gate, "one"), "two"])
    app = PromptHarnessApp(db=studio_db(tmp_path), client=client)
    async with app.run_test() as pilot:
        pane = await open_studio(app, pilot)
        await pane.add_case(Case(name="c1"))
        app.query_one("#cases", ListView).focus()
        await pilot.press("R")
        await pilot.pause()
        assert "running" in str(app.query_one("#studio-status", Label).render()).lower()
        await pilot.press("R")
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert not app.screen.query("#save-name")
        assert toasts(app, "already running") >= 3
        gate.set()
        await settle(app, pilot)
        assert len(client.calls) == 1
        assert "running" not in str(app.query_one("#studio-status", Label).render()).lower()


async def test_save_filters_results_by_judge(tmp_path):
    db = studio_db(tmp_path)
    app = PromptHarnessApp(db=db, client=FakeClient(["a"]))
    async with app.run_test() as pilot:
        pane = await open_studio(app, pilot)
        await pane.add_case(Case(name="c1"))  # no judge prompt: judge is never called
        app.query_one("#judge", Select).value = "p:m2"
        await pilot.pause()
        await _run_and_save(app, pilot, pane, "with-judge")
        run = db.get_run(db.get_harness("with-judge").accepted_run_id)
        assert str(run.judge_model) == "p:m2"
        app.query_one("#judge", Select).clear()
        await pilot.pause()
        app.query_one("#cases", ListView).focus()
        await pilot.press("s")
        await pilot.pause()
        app.screen.query_one("#save-name", Input).value = "no-judge"
        app.screen.query_one("#save-submit").press()
        await pilot.pause()
        assert db.get_harness("no-judge").accepted_run_id is None


async def test_judge_error_verdict_before_save_keeps_flag(tmp_path):
    db = studio_db(tmp_path)
    app = PromptHarnessApp(db=db, client=FakeClient(["answer", "garbage", "garbage"]))
    async with app.run_test() as pilot:
        pane = await open_studio(app, pilot)
        app.query_one("#judge", Select).value = "p:m2"
        await pane.add_case(Case(name="c1", expectation=Expectation(judge_prompt="ok?")))
        lv = app.query_one("#cases", ListView)
        lv.focus()
        await pilot.press("r")
        await settle(app, pilot)
        assert pane.result_for("c1").status == "judge_error"
        await pilot.press("v", "y")
        await pilot.pause()
        assert pane.result_for("c1").status == "pass"
        await pilot.press("s")
        await pilot.pause()
        app.screen.query_one("#save-name", Input).value = "h"
        app.screen.query_one("#save-submit").press()
        await pilot.pause()
        rid = db.get_harness("h").accepted_run_id
        res = db.get_run(rid).results[0]
        assert (res.status, res.manual_verdict) == ("pass", True)
        assert pane.result_for("c1").status == "pass"
        lv.focus()
        await pilot.press("v", "c")  # clear verdict -> back to judge_error via the db flag
        await pilot.pause()
        assert db.get_run(rid).results[0].status == "judge_error"
        assert pane.result_for("c1").status == "judge_error"


async def test_save_lookup_error_is_reported_not_crash(tmp_path):
    db = studio_db(tmp_path)
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test() as pilot:
        pane = await open_studio(app, pilot)
        await pane.add_case(Case(name="c1"))

        def boom(name):
            raise RuntimeError("db locked")

        db.get_harness = boom
        app.query_one("#cases", ListView).focus()
        await pilot.press("s")
        await pilot.pause()
        app.screen.query_one("#save-name", Input).value = "h"
        app.screen.query_one("#save-submit").press()
        await pilot.pause()
        assert app.is_running
        assert toasts(app, "db locked") == 1
    assert app.client.calls == []


async def test_studio_set_running_survives_teardown(tmp_path):
    app = PromptHarnessApp(db=make_db(tmp_path), client=FakeClient([]))
    async with app.run_test() as pilot:
        studio = app.query_one(StudioPane)
        studio._set_running(+1)
        await studio.query_one("#studio-status").remove()
        await pilot.pause()
        studio._set_running(-1)  # app quitting mid-run: status label already gone
        assert studio._active_runs == 0
    assert app.client.calls == []


async def test_case_form_normalises_document_paths(tmp_path):
    app = PromptHarnessApp(db=studio_db(tmp_path), client=FakeClient([]))
    async with app.run_test() as pilot:
        pane = await open_studio(app, pilot)
        app.query_one("#cases", ListView).focus()
        await pilot.press("n")
        await pilot.pause()
        s = app.screen
        s.query_one("#case-name", Input).value = "docs"
        s.query_one("#case-docs", Input).value = f"~/notes.txt, rel/../x.txt, {tmp_path}/a.txt"
        s.query_one("#case-submit").press()
        await pilot.pause()
        assert pane.cases[0].documents == [
            os.path.join(os.path.expanduser("~"), "notes.txt"),
            os.path.abspath("x.txt"),
            str(tmp_path / "a.txt"),
        ]
    assert app.client.calls == []


async def test_studio_output_wraps_to_the_pane_width_in_a_narrow_terminal(tmp_path):
    # RichLog renders at least `min_width` (78 by default) columns wide, which is wider than
    # the output pane in a 125-column terminal, so long results were cut off on the right.
    db = make_db(tmp_path)
    db.save_provider(Provider(name="p", base_url="https://p.test", api_key_env="K"))
    db.save_models("p", ["m1"])
    app = PromptHarnessApp(db=db, client=FakeClient(["word " * 60]))
    async with app.run_test(size=(125, 40)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        app.query_one("#model", Select).value = "p:m1"
        pane = app.query_one(StudioPane)
        await pane.add_case(Case(name="c1", input="hi"))
        app.query_one("#cases", ListView).focus()
        await pilot.press("r")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        log = app.query_one("#output", RichLog)
        width = log.scrollable_content_region.width
        assert log.lines
        assert max(strip.cell_length for strip in log.lines) <= width


async def _until(pilot, predicate, tries: int = 100) -> None:
    for _ in range(tries):
        if predicate():
            return
        await pilot.pause()
    raise AssertionError("condition not reached")


async def test_provider_form_sets_timeout_and_retries(tmp_path):
    db = make_db(tmp_path)
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("4", "a")
        await _until(pilot, lambda: bool(app.screen.query("#timeout")))
        await _fill(app, "lm", "http://localhost:1234/v1", "")
        app.screen.query_one("#timeout", Input).value = "300"
        app.screen.query_one("#max_retries", Input).value = "0"
        await pilot.click("#submit")
        await _until(pilot, lambda: db.get_provider("lm") is not None)
        got = db.get_provider("lm")
        assert (got.timeout, got.max_retries) == (300.0, 0)
        await _until(pilot, lambda: not app.screen.query("#submit"))
        # Blank means "use the default".
        await pilot.press("a")
        await _until(pilot, lambda: bool(app.screen.query("#timeout")))
        await _fill(app, "other", "http://localhost:1/v1", "")
        await pilot.click("#submit")
        await _until(pilot, lambda: db.get_provider("other") is not None)
        got = db.get_provider("other")
        assert (got.timeout, got.max_retries) == (None, None)
    assert app.client.calls == []


async def test_provider_form_edit_prefills_timeout_and_preserves_other_fields(tmp_path):
    db = make_db(tmp_path)
    db.save_provider(Provider(name="acme", base_url="https://a.test", api_key_env="K1",
                              enabled=False, timeout=5.0, max_retries=1, headers={"x": "y"}))
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("4", "e")
        await _until(pilot, lambda: bool(app.screen.query("#timeout")))
        assert app.screen.query_one("#timeout", Input).value == "5.0"
        assert app.screen.query_one("#max_retries", Input).value == "1"
        app.screen.query_one("#timeout", Input).value = "120"
        app.screen.query_one("#max_retries", Input).value = ""
        await pilot.click("#submit")
        await _until(pilot, lambda: db.get_provider("acme").timeout == 120.0)
        got = db.get_provider("acme")
        assert got.max_retries is None
        assert got.enabled is False and got.headers == {"x": "y"} and got.api_key_env == "K1"
    assert app.client.calls == []


async def test_provider_form_rejects_bad_timeout_and_retries(tmp_path):
    db = make_db(tmp_path)
    app = PromptHarnessApp(db=db, client=FakeClient([]))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("4", "a")
        await _until(pilot, lambda: bool(app.screen.query("#timeout")))
        await _fill(app, "lm", "http://localhost:1234/v1", "")
        for field, bad in (("#timeout", "0"), ("#timeout", "-3"), ("#timeout", "abc"),
                           ("#timeout", "nan"), ("#max_retries", "-1"), ("#max_retries", "2.5")):
            app.screen.query_one("#timeout", Input).value = ""
            app.screen.query_one("#max_retries", Input).value = ""
            app.screen.query_one(field, Input).value = bad
            app.clear_notifications()
            app.screen.query_one("#submit").press()
            await _until(pilot, lambda: len(app._notifications) > 0)
            assert app.screen.query("#submit"), (field, bad)
            assert db.get_provider("lm") is None, (field, bad)
            word = "Timeout" if field == "#timeout" else "Max retries"
            assert any(word in n.message for n in app._notifications), (field, bad)
    assert app.client.calls == []


async def test_provider_form_fits_in_80x24(tmp_path):
    app = PromptHarnessApp(db=make_db(tmp_path), client=FakeClient([]))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("4", "a")
        await _until(pilot, lambda: bool(app.screen.query("#timeout")))
        form = app.screen.query_one("#form").region
        assert form.y >= 0 and form.bottom <= 24, form
