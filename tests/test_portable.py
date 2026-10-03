import pytest

from promptharness.core.db import Database
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
