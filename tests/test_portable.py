from pathlib import Path

import pytest

from promptharness.core.db import Database
from promptharness.core.examples import read_example
from promptharness.core.models import (
    Case, CaseResult, Harness, Match, Expectation, ModelRef, PromptVersion, Run,
)
from promptharness.core.portable import (
    FORMAT_VERSION, PortableError, export_harness, import_harness, parse_harness,
)


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    d.migrate()
    return d


def make_harness(tmp_path, name="h"):
    doc = tmp_path / "doc.txt"
    doc.write_text("doc body")
    return Harness(
        name=name,
        description="d",
        prompt=PromptVersion(system="s", template="{{input}}", temperature=0.2),
        cases=[
            Case(name="a", input="x", documents=[str(doc)],
                 expectation=Expectation(must_include=[Match(pattern="hi")])),
            Case(name="b", input="y"),
        ],
        accepted_model=ModelRef(provider="openai", model="gpt-4o"),
    )


def seed(db, tmp_path):
    h = make_harness(tmp_path)
    db.save_harness(h)
    run = Run(
        harness="h", prompt_hash=h.prompt.hash, model=h.accepted_model, started_at="t",
        results=[CaseResult(case_name="a", status="pass", output="OA"),
                 CaseResult(case_name="b", status="pass", output="OB")],
    )
    rid = db.save_run(run)
    db.set_accepted("h", h.accepted_model, rid)
    return h


@pytest.mark.parametrize("fmt", ["yaml", "json"])
def test_roundtrip_equal(db, tmp_path, fmt):
    h = seed(db, tmp_path)
    text = export_harness(db, "h", fmt)
    parsed, outs = parse_harness(text)
    assert parsed == h.model_copy(update={"accepted_run_id": None})
    assert outs == {"a": "OA", "b": "OB"}
    assert parse_harness(text, fmt)[0] == parsed


def test_export_contains_no_secrets(db, tmp_path):
    from promptharness.core.models import Provider
    db.save_provider(Provider(name="openai", base_url="http://x", api_key_env="SECRET_ENV_KEY"))
    seed(db, tmp_path)
    text = export_harness(db, "h")
    assert "SECRET_ENV_KEY" not in text
    assert "api_key" not in text
    assert "openai:gpt-4o" in text


def test_inline_documents_included(db, tmp_path):
    seed(db, tmp_path)
    assert "document_texts" not in export_harness(db, "h")
    text = export_harness(db, "h", inline_documents=True)
    assert "doc body" in text
    parsed, _ = parse_harness(text)
    assert parsed.cases[0].name == "a"


def test_unknown_future_format_version_rejected(db):
    with pytest.raises(PortableError):
        import_harness(db, f"format_version: {FORMAT_VERSION + 1}\nname: z\n")
    assert db.list_harnesses() == []


def test_missing_format_version_rejected(db):
    with pytest.raises(PortableError):
        import_harness(db, "name: z\n")
    assert db.list_harnesses() == []


def test_duplicate_case_names_rejected_before_db_write(db):
    text = (
        "format_version: 1\nname: z\nprompt: {template: t}\n"
        "cases: [{name: a}, {name: a}]\n"
    )
    with pytest.raises(PortableError, match="duplicate"):
        import_harness(db, text)
    assert db.list_harnesses() == []


def test_import_existing_requires_overwrite(db, tmp_path):
    seed(db, tmp_path)
    text = export_harness(db, "h")
    with pytest.raises(PortableError, match="harness 'h' exists"):
        import_harness(db, text)


def test_import_overwrite_replaces(db, tmp_path):
    seed(db, tmp_path)
    text = export_harness(db, "h").replace("description: d", "description: new")
    import_harness(db, text, overwrite=True)
    assert db.get_harness("h").description == "new"


def test_import_restores_accepted_outputs_as_accepted_run(db, tmp_path):
    seed(db, tmp_path)
    text = export_harness(db, "h", "json")
    db.delete_harness("h")
    h = import_harness(db, text)
    assert h.accepted_run_id is not None
    run = db.get_run(h.accepted_run_id)
    assert run.model == ModelRef(provider="openai", model="gpt-4o")
    assert {r.case_name: r.output for r in run.results} == {"a": "OA", "b": "OB"}
    assert all(r.status == "manual" for r in run.results)
    assert all(r.request == {} and r.response == {} for r in run.results)


def test_malformed_yaml_raises_portable_error():
    with pytest.raises(PortableError):
        parse_harness("a: [unclosed\n  : :")
    with pytest.raises(PortableError):
        parse_harness("{bad json", "json")


def test_inline_documents_restored_on_import(db, tmp_path):
    from promptharness.core.render import load_documents
    h = seed(db, tmp_path)
    text = export_harness(db, "h", inline_documents=True)
    db.delete_harness("h")
    import os
    os.remove(h.cases[0].documents[0])
    out = import_harness(db, text)
    path = out.cases[0].documents[0]
    assert path != h.cases[0].documents[0]
    assert str(tmp_path / "ph-home") in path
    assert load_documents([path])[0].text == "doc body"


def test_existing_document_paths_left_alone(db, tmp_path):
    h = seed(db, tmp_path)
    text = export_harness(db, "h", inline_documents=True)
    out = import_harness(db, text, overwrite=True)
    assert out.cases[0].documents == h.cases[0].documents


def test_inline_document_path_traversal_neutralised(db, tmp_path):
    import json
    text = json.dumps({
        "format_version": 1, "name": "h", "prompt": {"template": "t"},
        "cases": [{"name": "a", "documents": ["../evil"],
                   "document_texts": [{"name": "../evil", "text": "pwn"}]}],
    })
    out = import_harness(db, text)
    home = tmp_path / "ph-home"
    p = out.cases[0].documents[0]
    from pathlib import Path
    base = (home / "documents" / "h").resolve()
    assert Path(p).resolve().parent == base
    assert Path(p).read_text() == "pwn"
    assert not (home / "documents" / "evil").exists()
    assert not (home / "evil").exists()


def test_export_unreadable_inline_document_raises(db, tmp_path):
    h = seed(db, tmp_path)
    import os
    os.remove(h.cases[0].documents[0])
    with pytest.raises(PortableError, match="doc.txt"):
        export_harness(db, "h", inline_documents=True)


def _snapshot(db):
    return ([h.model_dump() for h in db.list_harnesses()], len(db.list_runs()))


def test_import_atomic_fresh(db, tmp_path, monkeypatch):
    seed(db, tmp_path)
    text = export_harness(db, "h")
    db.delete_harness("h")
    before = _snapshot(db)
    def boom(run):
        raise RuntimeError("boom")
    monkeypatch.setattr(db, "save_run", boom)
    with pytest.raises(PortableError):
        import_harness(db, text)
    assert _snapshot(db) == before


def test_import_atomic_overwrite(db, tmp_path, monkeypatch):
    seed(db, tmp_path)
    text = export_harness(db, "h").replace("description: d", "description: new")
    before = _snapshot(db)
    def boom(run):
        raise RuntimeError("boom")
    monkeypatch.setattr(db, "save_run", boom)
    with pytest.raises(PortableError):
        import_harness(db, text, overwrite=True)
    assert _snapshot(db) == before


def test_accepted_outputs_without_model_rejected(db):
    text = ("format_version: 1\nname: z\nprompt: {template: t}\ncases: [{name: a}]\n"
            "accepted_outputs: {a: x}\n")
    with pytest.raises(PortableError, match="accepted_model"):
        import_harness(db, text)
    assert db.list_harnesses() == []


def test_accepted_outputs_unknown_case_rejected(db):
    text = ("format_version: 1\nname: z\nprompt: {template: t}\ncases: [{name: a}]\n"
            "accepted_model: p:m\naccepted_outputs: {ghost: x}\n")
    with pytest.raises(PortableError, match="ghost"):
        import_harness(db, text)
    assert db.list_harnesses() == []


EXAMPLE_TEXT = read_example("summarize")


def test_example_harness_parses():
    harness, outputs = parse_harness(EXAMPLE_TEXT)
    assert len(harness.cases) == 2
    assert harness.accepted_model is not None
    assert set(outputs) == {c.name for c in harness.cases}
    assert all(not c.documents for c in harness.cases)
    exps = [c.expectation for c in harness.cases]
    assert any(e.must_include for e in exps)
    assert any(e.json_schema for e in exps)


def test_example_harness_imports(db):
    h = import_harness(db, EXAMPLE_TEXT)
    assert h.accepted_run_id is not None
    assert len(h.cases) == 2


def test_export_omits_default_name_and_includes_custom(db):
    import yaml

    base = Harness(
        name="h", prompt=PromptVersion(template="{{ input }}"),
        cases=[Case(name="a", input="x")],
    )
    db.save_harness(base)
    text = export_harness(db, "h")
    assert "documents_name" not in yaml.safe_load(text)["prompt"]
    assert parse_harness(text)[0].prompt.documents_name == "documents"

    custom = base.model_copy(
        update={
            "name": "c",
            "prompt": PromptVersion(template="{{ doc }}", documents_name="doc"),
        }
    )
    db.save_harness(custom)
    text = export_harness(db, "c")
    assert yaml.safe_load(text)["prompt"]["documents_name"] == "doc"
    assert parse_harness(text)[0].prompt.documents_name == "doc"
    assert "documents_name" in export_harness(db, "c", "json")
