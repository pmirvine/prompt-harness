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
    # Legacy inline exports carried absolute paths; existing files are kept.
    h = seed(db, tmp_path)
    text = _to_legacy(export_harness(db, "h", inline_documents=True), h)
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


# ---- binary documents -------------------------------------------------------

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe"
    b"\x02\xfe\xa7\x9a\xa0\xa0\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _binary_harness(tmp_path, files: dict[str, bytes], name="h"):
    paths = []
    for rel, data in files.items():
        p = tmp_path / "src" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        paths.append(str(p))
    return Harness(
        name=name,
        prompt=PromptVersion(template="{{input}}"),
        cases=[Case(name="a", input="x", documents=paths)],
    )


def test_binary_documents_round_trip(db, tmp_path):
    import shutil
    from docfixtures import make_docx, make_pdf
    from promptharness.core.documents import load_documents
    h = _binary_harness(tmp_path, {
        "invoice.docx": make_docx(["Invoice total 42"]),
        "invoice.pdf": make_pdf(["Hello pdf"]),
        "pic.png": PNG,
    })
    db.save_harness(h)
    before = [(d.kind, d.text) for d in load_documents(h.cases[0].documents)]
    original_bytes = [Path(p).read_bytes() for p in h.cases[0].documents]
    text = export_harness(db, "h", inline_documents=True)
    db.delete_harness("h")
    shutil.rmtree(tmp_path / "src")
    out = import_harness(db, text)
    paths = out.cases[0].documents
    assert all(str(tmp_path / "ph-home") in p and Path(p).exists() for p in paths)
    assert [Path(p).read_bytes() for p in paths] == original_bytes
    assert [(d.kind, d.text) for d in load_documents(paths)] == before


def test_text_and_binary_use_different_keys(db, tmp_path):
    import yaml
    from docfixtures import make_pdf
    h = _binary_harness(tmp_path, {"n.txt": b"hello", "r.pdf": make_pdf(["x"]) + b"\n%\xe2\xe3\xcf\xd3\n"})
    db.save_harness(h)
    data = yaml.safe_load(export_harness(db, "h", inline_documents=True))
    case = data["cases"][0]
    assert [e["name"] for e in case["document_texts"]] == ["n.txt"]
    (entry,) = case["document_files"]
    assert entry["name"] == "r.pdf"
    assert entry["mime"] == "application/pdf"
    assert set(entry) == {"name", "mime", "base64"}


def test_existing_binary_paths_are_left_alone(db, tmp_path):
    from docfixtures import make_pdf
    h = _binary_harness(tmp_path, {"r.pdf": make_pdf(["x"])})
    db.save_harness(h)
    text = _to_legacy(export_harness(db, "h", inline_documents=True), h)
    out = import_harness(db, text, overwrite=True)
    assert out.cases[0].documents == h.cases[0].documents


def test_oversized_file_is_refused(db, tmp_path, monkeypatch):
    from promptharness.core import portable
    h = _binary_harness(tmp_path, {"big.pdf": b"%PDF-" + b"\x00" * 50})
    db.save_harness(h)
    monkeypatch.setattr(portable, "MAX_INLINE_BYTES", 10)
    with pytest.raises(PortableError, match="big.pdf") as ei:
        export_harness(db, "h", inline_documents=True)
    assert "larger than 10 MB" in str(ei.value)


def test_same_basename_in_different_folders_do_not_collide(db, tmp_path):
    import shutil
    from docfixtures import make_pdf
    h = _binary_harness(tmp_path, {
        "a/report.pdf": make_pdf(["first"]), "b/report.pdf": make_pdf(["second"]),
    })
    originals = [Path(p).read_bytes() for p in h.cases[0].documents]
    db.save_harness(h)
    text = export_harness(db, "h", inline_documents=True)
    db.delete_harness("h")
    shutil.rmtree(tmp_path / "src")
    out = import_harness(db, text)
    paths = out.cases[0].documents
    assert len({Path(p).name for p in paths}) == 2
    assert [Path(p).read_bytes() for p in paths] == originals


def test_text_and_binary_same_basename_do_not_collide(db, tmp_path):
    import shutil
    h = _binary_harness(tmp_path, {"a/x.dat": b"plain text", "b/x.dat": b"\x00\x01\x02bin"})
    db.save_harness(h)
    text = export_harness(db, "h", inline_documents=True)
    db.delete_harness("h")
    shutil.rmtree(tmp_path / "src")
    out = import_harness(db, text)
    p1, p2 = out.cases[0].documents
    assert p1 != p2
    assert Path(p1).read_bytes() == b"plain text"
    assert Path(p2).read_bytes() == b"\x00\x01\x02bin"


def test_bad_base64_is_a_portable_error_and_writes_nothing(db, tmp_path):
    import json
    text = json.dumps({
        "format_version": 1, "name": "h", "prompt": {"template": "t"},
        "cases": [{"name": "a", "documents": ["/gone/x.pdf"],
                   "document_files": [{"name": "/gone/x.pdf", "mime": "application/pdf",
                                       "base64": "!!not base64!!"}]}],
    })
    with pytest.raises(PortableError, match="invalid document_files in case 'a'"):
        import_harness(db, text)
    assert db.get_harness("h") is None
    assert not (tmp_path / "ph-home" / "documents").exists()
    with pytest.raises(PortableError, match="invalid document_files"):
        parse_harness(text)


@pytest.mark.parametrize("entries", [
    [{"name": "x"}], [{"base64": "AA=="}], ["str"], "notalist", [{"name": "x", "base64": 5}],
])
def test_malformed_document_files_rejected(db, entries):
    import json
    text = json.dumps({
        "format_version": 1, "name": "h", "prompt": {"template": "t"},
        "cases": [{"name": "a", "document_files": entries}],
    })
    with pytest.raises(PortableError, match="invalid document_files in case 'a'"):
        parse_harness(text)


def test_path_traversal_name_in_document_files_is_neutralised(db, tmp_path):
    import base64
    import json
    text = json.dumps({
        "format_version": 1, "name": "h", "prompt": {"template": "t"},
        "cases": [{"name": "a", "documents": ["../evil.bin"],
                   "document_files": [{"name": "../evil.bin", "mime": "x",
                                       "base64": base64.b64encode(b"\x00pwn").decode()}]}],
    })
    out = import_harness(db, text)
    home = tmp_path / "ph-home"
    p = out.cases[0].documents[0]
    assert Path(p).resolve().parent == (home / "documents" / "h").resolve()
    assert Path(p).read_bytes() == b"\x00pwn"
    assert not (home / "documents" / "evil.bin").exists()
    assert not (home / "evil.bin").exists()


def test_restore_write_failure_is_portable_error_and_rolls_back(db, tmp_path, monkeypatch):
    import base64
    import json
    text = json.dumps({
        "format_version": 1, "name": "h", "prompt": {"template": "t"},
        "cases": [{"name": "a", "documents": ["/gone/x.bin"],
                   "document_files": [{"name": "/gone/x.bin", "mime": "x",
                                       "base64": base64.b64encode(b"\x00").decode()}]}],
    })
    def boom(self, data):
        raise OSError("disk full")
    monkeypatch.setattr(Path, "write_bytes", boom)
    with pytest.raises(PortableError, match="disk full"):
        import_harness(db, text)
    assert db.get_harness("h") is None


# ---- inline export names and restore integrity (fix wave M1-M3, R9) ----------


def _to_legacy(text, harness):
    """Rewrite an inline export to the old format that used absolute source paths."""
    import yaml
    data = yaml.safe_load(text)
    for cd, case in zip(data["cases"], harness.cases, strict=True):
        mapping = dict(zip(cd["documents"], case.documents, strict=True))
        cd["documents"] = list(case.documents)
        for key in ("document_texts", "document_files"):
            for e in cd.get(key, []):
                e["name"] = mapping[e["name"]]
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _multi_case_harness(tmp_path, cases: dict[str, dict[str, bytes]], name="h"):
    built = []
    for cname, files in cases.items():
        paths = []
        for rel, data in files.items():
            p = tmp_path / "src" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            paths.append(str(p))
        built.append(Case(name=cname, input="x", documents=paths))
    return Harness(name=name, prompt=PromptVersion(template="{{input}}"), cases=built)


@pytest.mark.parametrize("fmt", ["yaml", "json"])
def test_inline_export_contains_no_local_paths(db, tmp_path, fmt):
    from docfixtures import make_pdf
    h = _multi_case_harness(tmp_path, {
        "a": {"one/report.pdf": make_pdf(["first"]), "notes.txt": b"hello"},
        "b": {"two/report.pdf": make_pdf(["second"])},
    })
    db.save_harness(h)
    text = export_harness(db, "h", fmt, inline_documents=True)
    assert str(tmp_path) not in text and str(Path.home()) not in text
    assert str(tmp_path.resolve()) not in text and "/src/" not in text
    import json
    import yaml
    data = json.loads(text) if fmt == "json" else yaml.safe_load(text)
    for c in data["cases"]:
        names = [*c["documents"]] + [
            e["name"] for k in ("document_texts", "document_files") for e in c.get(k, [])
        ]
        assert names and all("/" not in n and "\\" not in n for n in names), names


def test_inline_export_maps_same_base_names_deterministically(db, tmp_path):
    import shutil
    import yaml
    from docfixtures import make_pdf
    first, second = make_pdf(["first"]), make_pdf(["second"])
    h = _multi_case_harness(tmp_path, {
        "a": {"one/report.pdf": first},
        "b": {"two/report.pdf": second},
    })
    db.save_harness(h)
    text = export_harness(db, "h", inline_documents=True)
    data = yaml.safe_load(text)
    assert data["cases"][0]["documents"] == ["report.pdf"]
    assert data["cases"][1]["documents"] == ["2_report.pdf"]
    def entry_names(c):
        return [e["name"] for k in ("document_texts", "document_files") for e in c.get(k, [])]
    assert entry_names(data["cases"][0]) == ["report.pdf"]
    assert entry_names(data["cases"][1]) == ["2_report.pdf"]
    db.delete_harness("h")
    shutil.rmtree(tmp_path / "src")
    out = import_harness(db, text)
    assert Path(out.cases[0].documents[0]).read_bytes() == first
    assert Path(out.cases[1].documents[0]).read_bytes() == second
    assert Path(out.cases[0].documents[0]).name == "report.pdf"
    assert Path(out.cases[1].documents[0]).name == "2_report.pdf"


def test_shared_document_maps_to_one_name_and_is_restored_once(db, tmp_path):
    import shutil
    import yaml
    shared = tmp_path / "src" / "shared.txt"
    shared.parent.mkdir(parents=True)
    shared.write_bytes(b"shared body")
    h = Harness(name="h", prompt=PromptVersion(template="{{input}}"), cases=[
        Case(name="a", input="x", documents=[str(shared)]),
        Case(name="b", input="y", documents=[str(shared)]),
    ])
    db.save_harness(h)
    text = export_harness(db, "h", inline_documents=True)
    data = yaml.safe_load(text)
    assert [c["documents"] for c in data["cases"]] == [["shared.txt"], ["shared.txt"]]
    db.delete_harness("h")
    shutil.rmtree(tmp_path / "src")
    out = import_harness(db, text)
    assert out.cases[0].documents == out.cases[1].documents
    restored = list((tmp_path / "ph-home" / "documents" / "h").iterdir())
    assert [p.name for p in restored] == ["shared.txt"]
    assert restored[0].read_bytes() == b"shared body"


def test_non_inline_export_keeps_original_paths(db, tmp_path):
    import yaml
    h = seed(db, tmp_path)
    data = yaml.safe_load(export_harness(db, "h"))
    assert data["cases"][0]["documents"] == h.cases[0].documents


def test_import_restores_bare_names_even_if_cwd_has_same_named_file(db, tmp_path, monkeypatch):
    h = seed(db, tmp_path)
    text = export_harness(db, "h", inline_documents=True)
    db.delete_harness("h")
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    (cwd / "doc.txt").write_text("WRONG FILE")
    monkeypatch.chdir(cwd)
    out = import_harness(db, text)
    p = Path(out.cases[0].documents[0])
    assert p.is_absolute()
    assert p.parent == tmp_path / "ph-home" / "documents" / "h"
    assert p.read_bytes() == b"doc body"
    assert (cwd / "doc.txt").read_text() == "WRONG FILE"
    assert h.cases[0].documents[0] != str(p)


def test_legacy_absolute_path_export_with_existing_files_is_left_alone(db, tmp_path):
    h = seed(db, tmp_path)
    text = _to_legacy(export_harness(db, "h", inline_documents=True), h)
    assert h.cases[0].documents[0] in text
    out = import_harness(db, text, overwrite=True)
    assert out.cases[0].documents == h.cases[0].documents
    assert not (tmp_path / "ph-home" / "documents").exists()


def test_restore_same_name_different_content_kept_apart(db):
    import json
    text = json.dumps({
        "format_version": 1, "name": "h", "prompt": {"template": "t"},
        "cases": [
            {"name": "a", "documents": ["a.txt"], "document_texts": [{"name": "a.txt", "text": "ONE"}]},
            {"name": "b", "documents": ["a.txt"], "document_texts": [{"name": "a.txt", "text": "TWO"}]},
        ],
    })
    out = import_harness(db, text)
    p1, p2 = out.cases[0].documents[0], out.cases[1].documents[0]
    assert p1 != p2
    assert Path(p1).read_bytes() == b"ONE" and Path(p2).read_bytes() == b"TWO"


def test_restore_collision_rename_never_overwrites_another_document(db):
    import json
    text = json.dumps({
        "format_version": 1, "name": "h", "prompt": {"template": "t"},
        "cases": [
            {"name": "c1", "documents": ["/x/2_a.txt", "/x/a.txt"],
             "document_texts": [{"name": "/x/2_a.txt", "text": "TWO_A"},
                                {"name": "/x/a.txt", "text": "A1"}]},
            {"name": "c2", "documents": ["/y/a.txt"],
             "document_texts": [{"name": "/y/a.txt", "text": "A2"}]},
        ],
    })
    out = import_harness(db, text)
    paths = [*out.cases[0].documents, *out.cases[1].documents]
    assert len(set(paths)) == 3
    assert [Path(p).read_bytes() for p in paths] == [b"TWO_A", b"A1", b"A2"]


@pytest.mark.parametrize("fmt", ["yaml", "json"])
def test_crlf_text_and_binary_round_trip_bytes_exactly(db, tmp_path, fmt):
    import shutil
    files = {
        "crlf.txt": b"line one\r\nline two\r\n",
        "ascii.pdf": b"%PDF-1.4\r\n1 0 obj\r\n<< >>\r\nendobj\r\n%%EOF\r\n",
        "nel.txt": "x\u0085y".encode("utf-8"),
    }
    h = _binary_harness(tmp_path, files)
    db.save_harness(h)
    text = export_harness(db, "h", fmt, inline_documents=True)
    db.delete_harness("h")
    shutil.rmtree(tmp_path / "src")
    out = import_harness(db, text)
    assert [Path(p).read_bytes() for p in out.cases[0].documents] == list(files.values())


def test_yaml_unsafe_text_is_exported_as_a_file(db, tmp_path):
    import yaml
    h = _binary_harness(tmp_path, {"nel.txt": "x\u0085y".encode("utf-8"), "ok.txt": b"fine"})
    db.save_harness(h)
    case = yaml.safe_load(export_harness(db, "h", inline_documents=True))["cases"][0]
    assert [e["name"] for e in case["document_texts"]] == ["ok.txt"]
    assert [e["name"] for e in case["document_files"]] == ["nel.txt"]
