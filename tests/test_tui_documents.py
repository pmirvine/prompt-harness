"""TUI tests for documents in the Studio: the documents name, result details, case form."""

from __future__ import annotations

import docfixtures
from conftest import FakeClient
from textual.widgets import Input, Label, ListView, RichLog, Select, TabbedContent, TextArea

from promptharness.core.db import Database
from promptharness.core.models import Case, Harness, ModelRef, PromptVersion, Provider
from promptharness.tui.app import PromptHarnessApp
from promptharness.tui.studio import StudioPane


def make_db(tmp_path) -> Database:
    db = Database(tmp_path / "tui.db")
    db.save_provider(Provider(name="p", base_url="https://p.test", api_key_env="K"))
    db.save_models("p", ["m1"])
    return db


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


async def open_studio(app, pilot) -> StudioPane:
    await pilot.press("2")
    await pilot.pause()
    app.query_one("#model", Select).value = "p:m1"
    await pilot.pause()
    return app.query_one(StudioPane)


def log_text(app) -> str:
    log = app.query_one("#output", RichLog)
    return "\n".join("".join(seg.text for seg in strip) for strip in log.lines)


async def run_case(app, pilot) -> None:
    app.query_one("#cases", ListView).focus()
    await pilot.press("r")
    await settle(app, pilot)


async def test_docs_as_name_is_used_when_running(tmp_path):
    note = tmp_path / "note.txt"
    note.write_text("the secret text")
    client = FakeClient(["done"])
    app = PromptHarnessApp(db=make_db(tmp_path), client=client)
    async with app.run_test() as pilot:
        pane = await open_studio(app, pilot)
        app.query_one("#documents_name", Input).value = "doc"
        app.query_one("#template", TextArea).text = "{{ doc[0].text }}"
        await pane.add_case(Case(name="c1", documents=[str(note)]))
        await run_case(app, pilot)
        assert len(client.calls) == 1
        assert client.calls[0]["messages"][-1]["content"] == "the secret text"
        assert pane.current_prompt().documents_name == "doc"


async def test_invalid_name_shows_a_notification_and_does_not_run(tmp_path):
    client = FakeClient(["never"])
    app = PromptHarnessApp(db=make_db(tmp_path), client=client)
    async with app.run_test() as pilot:
        pane = await open_studio(app, pilot)
        app.query_one("#documents_name", Input).value = "not valid"
        await pane.add_case(Case(name="c1", input="hi"))
        await run_case(app, pilot)
        assert any("documents_name" in n.message and n.severity == "error"
                   for n in app._notifications)
        assert pane.result_for("c1") is None
    assert client.calls == []


async def test_loading_a_harness_with_a_custom_name_shows_it(tmp_path):
    app = PromptHarnessApp(db=make_db(tmp_path), client=FakeClient([]))
    async with app.run_test() as pilot:
        pane = await open_studio(app, pilot)
        h = Harness(name="h", prompt=PromptVersion(template="{{ files }}",
                                                   documents_name="files"),
                    cases=[Case(name="c1")])
        await pane.load_harness(h, ModelRef.parse("p:m1"))
        await pilot.pause()
        assert app.query_one("#documents_name", Input).value == "files"
        assert pane.current_prompt().documents_name == "files"
        assert not pane.is_dirty()
        # A default-named harness shows a blank field (the placeholder says "documents").
        await pane.load_harness(Harness(name="d", prompt=PromptVersion(template="x"), cases=[]),
                              None)
        await pilot.pause()
        assert app.query_one("#documents_name", Input).value == ""
        assert pane.current_prompt().documents_name == "documents"
    assert app.client.calls == []


async def test_name_participates_in_dirty_check_and_history(tmp_path):
    app = PromptHarnessApp(db=make_db(tmp_path), client=FakeClient([]))
    async with app.run_test() as pilot:
        pane = await open_studio(app, pilot)
        name = app.query_one("#documents_name", Input)
        assert not pane.is_dirty()
        name.value = "doc"
        await pilot.pause()
        assert pane.is_dirty()
        # Custom → another custom name is noticed too.
        h = Harness(name="h", prompt=PromptVersion(template="x", documents_name="files"),
                    cases=[])
        await pane.load_harness(h, None)
        await pilot.pause()
        assert not pane.is_dirty()
        pane.HISTORY_PAUSE = 0.05
        name.value = "attachments"
        await pilot.pause()
        assert pane.is_dirty()
        await until(pilot, lambda: len(pane.history) >= 2, tries=400)
        app.query_one("#cases", ListView).focus()
        await pilot.press("ctrl+z")
        await pilot.pause()
        assert name.value == "files"
        assert not pane.is_dirty()
        await pilot.press("ctrl+y")
        await pilot.pause()
        assert name.value == "attachments"
    assert app.client.calls == []


async def test_result_header_lists_attached_documents_and_warnings(tmp_path):
    note = tmp_path / "note.txt"
    note.write_text("hello")
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(docfixtures.make_pdf(["Alpha", ""]))
    app = PromptHarnessApp(db=make_db(tmp_path), client=FakeClient(["answer"]))
    # Wide enough that the output pane does not wrap the lines checked below.
    async with app.run_test(size=(200, 40)) as pilot:
        pane = await open_studio(app, pilot)
        await pane.add_case(Case(name="c1", documents=[str(note), str(pdf)]))
        await run_case(app, pilot)
        text = log_text(app)
        assert "documents: note.txt (text) · report.pdf (text, 2 pages)" in pane.output_text
        assert "documents:" in text and "note.txt" in text and "report.pdf" in text
        assert "report.pdf: page 2 has no extractable text" in text


async def test_case_form_documents_label_lists_formats(tmp_path):
    app = PromptHarnessApp(db=make_db(tmp_path), client=FakeClient([]))
    async with app.run_test() as pilot:
        await open_studio(app, pilot)
        app.query_one("#cases", ListView).focus()
        await pilot.press("n")
        await until(pilot, lambda: bool(app.screen.query("#case-docs")))
        labels = [str(lbl.render()) for lbl in app.screen.query(Label)]
        assert any("(text, PDF, Word, PowerPoint, Excel, OpenDocument, RTF, images)" in t
                   for t in labels)
    assert app.client.calls == []


async def test_typing_in_documents_name_does_not_trigger_keys(tmp_path):
    app = PromptHarnessApp(db=make_db(tmp_path), client=FakeClient([]))
    async with app.run_test() as pilot:
        await open_studio(app, pilot)
        name = app.query_one("#documents_name", Input)
        name.focus()
        await pilot.press("q", "2", "3", "r", "n")
        await pilot.pause()
        assert app.is_running
        assert app.query_one(TabbedContent).active == "studio"
        assert name.value == "q23rn"
        assert not app.screen.query("#case-submit")
    assert app.client.calls == []


def test_format_result_documents_line_kinds():
    from promptharness.core.models import CaseResult
    from promptharness.tui.studio_support import format_result

    docs = [{"name": "a.txt", "kind": "text", "mime": "text/plain", "chars": 3},
            {"name": "one.pdf", "kind": "text", "mime": "application/pdf", "chars": 9,
             "pages": 1},
            {"name": "c.png", "kind": "image", "mime": "image/png", "bytes": 10}]
    r = CaseResult(case_name="c", status="manual", output="out", request={"documents": docs})
    lines = format_result(r, ModelRef.parse("p:m")).plain.splitlines()
    assert lines[1] == "documents: a.txt (text) · one.pdf (text, 1 page) · c.png (image)"
    r = CaseResult(case_name="c", status="manual", output="out", request={"documents": []})
    assert "documents:" not in format_result(r, ModelRef.parse("p:m")).plain


def _template_label(app) -> str:
    return str(app.query_one("#template-label", Label).content)


async def test_template_label_follows_the_docs_as_name(tmp_path):
    app = PromptHarnessApp(db=make_db(tmp_path), client=FakeClient([]))
    async with app.run_test() as pilot:
        pane = await open_studio(app, pilot)
        assert _template_label(app) == "User template (Jinja2: input, documents)"
        name = app.query_one("#documents_name", Input)
        name.value = "doc"
        await until(pilot, lambda: _template_label(app) == "User template (Jinja2: input, doc)")
        for bad in ("not valid", "input", "class", ""):
            name.value = bad
            await until(pilot, lambda: _template_label(app)
                        == "User template (Jinja2: input, documents)")
        h = Harness(name="h", prompt=PromptVersion(template="{{ files }}", documents_name="files"),
                    cases=[Case(name="c1")])
        await pane.load_harness(h, None)
        await until(pilot, lambda: _template_label(app)
                    == "User template (Jinja2: input, files)")
        await pane.load_harness(Harness(name="d", prompt=PromptVersion(template="x"), cases=[]),
                                None)
        await until(pilot, lambda: _template_label(app)
                    == "User template (Jinja2: input, documents)")
    assert app.client.calls == []


async def test_top_row_fits_model_and_judge_selects_at_80_columns(tmp_path):
    for width, minimum in ((80, 12), (100, 17), (125, 29)):
        app = PromptHarnessApp(db=make_db(tmp_path), client=FakeClient([]))
        async with app.run_test(size=(width, 24)) as pilot:
            await open_studio(app, pilot)
            model = app.query_one("#model", Select).region
            judge = app.query_one("#judge", Select).region
            assert model.width >= minimum and judge.width >= minimum, (width, model, judge)
            top = app.query_one("#studio-top").region
            assert max(w.region.right for w in app.query_one("#studio-top").children) <= top.right
            name = app.query_one("#documents_name", Input)
            assert name.region.width >= 13
            assert "Docs as" in str(name.border_title or "") + str(name.tooltip or "")
