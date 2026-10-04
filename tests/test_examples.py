from __future__ import annotations

from pathlib import Path

import pytest

import promptharness

from promptharness.core.checks import run_checks
from promptharness.core.db import Database
from promptharness.core.examples import ExampleError, list_examples, read_example
from promptharness.core.portable import import_harness, parse_harness


GOOD = {
    "capital-of-france": "Paris",
    "extract-person-json": '{"name": "Ada Lovelace", "age": 36}',
    "three-colors": "red, green, blue",
}
BAD = {
    "capital-of-france": "The capital of France is London.",
    "extract-person-json": "Ada Lovelace is 36.",
    "three-colors": "red, green",
}


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    d.migrate()
    return d


@pytest.fixture
def quickstart():
    harness, outputs = parse_harness(read_example("quickstart"))
    return harness, outputs


def test_quickstart_is_model_agnostic_and_self_contained(quickstart):
    harness, outputs = quickstart
    assert [c.name for c in harness.cases] == list(GOOD)
    assert harness.accepted_model is None and outputs == {}
    assert all(not c.documents for c in harness.cases)


def test_quickstart_budget_fits_reasoning_models(quickstart):
    harness, _ = quickstart
    assert harness.prompt.max_tokens is not None and harness.prompt.max_tokens >= 1024


@pytest.mark.parametrize("name", list(GOOD))
def test_quickstart_checks_accept_good_and_reject_bad_answers(quickstart, name):
    harness, _ = quickstart
    exp = next(c for c in harness.cases if c.name == name).expectation
    good = run_checks(GOOD[name], exp)
    assert good and all(r.passed for r in good), good
    bad = run_checks(BAD[name], exp)
    assert any(not r.passed for r in bad), bad


def test_quickstart_imports(db):
    h = import_harness(db, read_example("quickstart"))
    assert h.name == "quickstart"
    assert db.get_harness("quickstart") is not None


def test_examples_are_bundled_inside_the_package():
    # A normal (non-editable) install only contains files under src/promptharness/.
    pkg = Path(promptharness.__file__).resolve().parent / "examples"
    assert (pkg / "quickstart.harness.yaml").is_file()
    assert (pkg / "summarize.harness.yaml").is_file()


def test_list_examples_names_and_descriptions():
    infos = list_examples()
    assert [i.name for i in infos] == ["mixed-formats", "quickstart", "summarize", "three-documents"]
    assert all(i.description.strip() for i in infos)
    # Descriptions come from the harness files, with folded YAML whitespace collapsed.
    assert all("\n" not in i.description for i in infos)


@pytest.mark.parametrize("name", ["quickstart", "summarize", "three-documents", "mixed-formats"])
def test_every_bundled_example_parses_and_imports(db, name):
    harness, _ = parse_harness(read_example(name))
    assert harness.cases
    assert import_harness(db, read_example(name)).name == harness.name


def test_unknown_example_lists_the_available_ones():
    with pytest.raises(ExampleError) as exc:
        read_example("nope")
    assert "nope" in str(exc.value)
    assert "quickstart" in str(exc.value) and "summarize" in str(exc.value)


@pytest.mark.parametrize("bad", ["", "../quickstart", "a/b", "quickstart.harness.yaml", "Quickstart"])
def test_example_names_cannot_escape_the_examples_folder(bad):
    with pytest.raises(ExampleError):
        read_example(bad)


# ---- document examples -------------------------------------------------------

import json  # noqa: E402
import sys  # noqa: E402

import yaml  # noqa: E402

from promptharness.core.documents import load_documents  # noqa: E402
from promptharness.core.render import render_user  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "docs" / "sample-documents"
EXAMPLES_DIR = ROOT / "src" / "promptharness" / "examples"
DOC_EXAMPLES = ["three-documents", "mixed-formats"]
GOOD_JSON = '{"total": 400.5, "earliest_due": "2026-11-01"}'
BAD_JSON = ['{"total": 300.0, "earliest_due": "2026-11-01"}', '{"total": 400.5, "earliest_due": "2026-11-15"}']


def _import(db, name):
    h = import_harness(db, read_example(name))
    return db.get_harness(h.name)


def _case(h, name):
    return next(c for c in h.cases if c.name == name)


def test_three_documents_example_parses_and_imports(db):
    h = _import(db, "three-documents")
    assert h.accepted_model is None
    for c in h.cases:
        docs = load_documents(c.documents)
        assert [d.kind for d in docs] == ["text"] * 3
        for d, n in zip(docs, ("1041", "1042", "1043")):
            assert n in d.text
        out = render_user(h.prompt.template, c.input, docs)
        pos = [out.index(d.text) for d in docs]
        assert pos == sorted(pos)
        assert all(d.name in out for d in docs)
    assert "documents[0]" in h.prompt.template and "documents[2]" in h.prompt.template


def test_mixed_formats_example_restores_binary_documents(db):
    h = _import(db, "mixed-formats")
    docs = load_documents(_case(h, "total-and-due-date").documents)
    assert [d.kind for d in docs] == ["text", "text", "image"]
    assert "1041" in docs[0].text and "1042" in docs[1].text
    assert docs[2].mime == "image/png"
    out = render_user(h.prompt.template, "q", docs)
    assert "Document 3: invoice-1043.png" in out


@pytest.mark.parametrize(
    "example,case,good,bads",
    [
        ("three-documents", "total-and-due-date", GOOD_JSON, BAD_JSON),
        ("mixed-formats", "total-and-due-date", GOOD_JSON, BAD_JSON),
        ("three-documents", "largest-invoice", "1043", ["1041", "The answer is 1042"]),
    ],
)
def test_document_checks_accept_good_and_reject_bad_answers(example, case, good, bads):
    harness, _ = parse_harness(read_example(example))
    exp = _case(harness, case).expectation
    res = run_checks(good, exp)
    assert res and all(r.passed for r in res), res
    for bad in bads:
        assert any(not r.passed for r in run_checks(bad, exp)), bad


def test_largest_invoice_judge_prompt_names_all_documents():
    harness, _ = parse_harness(read_example("three-documents"))
    jp = _case(harness, "largest-invoice").expectation.judge_prompt
    for i in range(3):
        assert "{{ documents[%d].name }}" % i in jp


def test_sample_documents_are_readable():
    docs = load_documents([str(SAMPLES / n) for n in ("invoice-1041.docx", "invoice-1042.pdf", "invoice-1043.png")])
    assert [d.kind for d in docs] == ["text", "text", "image"]
    assert "1041" in docs[0].text and "1042" in docs[1].text


def _split(text):
    data = yaml.safe_load(text)
    texts, files = {}, {}
    for c in data["cases"]:
        texts[c["name"]] = {t["name"]: t["text"] for t in c.pop("document_texts", [])}
        files[c["name"]] = {f["name"]: f for f in c.pop("document_files", [])}
        c["documents"] = [n for n in c["documents"]]
    return data, texts, files


def _loaded(tmp_path, tag, f):
    import base64

    p = tmp_path / tag / f["name"]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(base64.b64decode(f["base64"]))
    d = load_documents([str(p)])[0]
    return (d.kind, d.mime, d.text if d.kind == "text" else None)


@pytest.mark.parametrize("name", DOC_EXAMPLES)
def test_build_script_output_matches_committed_files(tmp_path, name):
    import build_document_examples as b

    b.build(tmp_path / "out", tmp_path / "samples")
    new, ntexts, nfiles = _split((tmp_path / "out" / f"{name}.harness.yaml").read_text("utf-8"))
    old, otexts, ofiles = _split((EXAMPLES_DIR / f"{name}.harness.yaml").read_text("utf-8"))
    assert new == old
    assert ntexts == otexts
    assert {c: {n: f["mime"] for n, f in d.items()} for c, d in nfiles.items()} == {
        c: {n: f["mime"] for n, f in d.items()} for c, d in ofiles.items()
    }
    for case in nfiles:
        for n in nfiles[case]:
            assert _loaded(tmp_path, "n", nfiles[case][n]) == _loaded(tmp_path, "o", ofiles[case][n])
    for fn in ("invoice-1041.docx", "invoice-1042.pdf", "invoice-1043.png"):
        a = load_documents([str(tmp_path / "samples" / fn)])[0]
        c = load_documents([str(SAMPLES / fn)])[0]
        assert (a.kind, a.mime, a.text if a.kind == "text" else None) == (
            c.kind, c.mime, c.text if c.kind == "text" else None)


@pytest.mark.parametrize("name", DOC_EXAMPLES)
def test_committed_examples_contain_no_local_paths(name):
    data = yaml.safe_load(read_example(name))
    for c in data["cases"]:
        for n in c["documents"]:
            assert "/" not in n and "\\" not in n and "tmp" not in n.lower()
        for t in [*c.get("document_texts", []), *c.get("document_files", [])]:
            assert "/" not in t["name"] and "tmp" not in t["name"].lower()


def test_three_documents_cli_import_restores_three_files_without_prefixes(monkeypatch):
    import asyncio
    import re

    from conftest import FakeClient
    from typer.testing import CliRunner

    from promptharness import cli, paths
    from promptharness.core.judge import run_judge
    from promptharness.core.models import Provider

    r = CliRunner().invoke(cli.app, ["import", "--example", "three-documents"])
    assert r.exit_code == 0, r.output
    h = Database(paths.db_path()).get_harness("three-documents")
    restored = sorted(p.name for p in (paths.home_dir() / "documents" / "three-documents").iterdir())
    assert restored == ["invoice-1041.txt", "invoice-1042.txt", "invoice-1043.txt"]
    assert h.cases[0].documents == h.cases[1].documents
    names = ["invoice-1041.txt", "invoice-1042.txt", "invoice-1043.txt"]
    fake = FakeClient(['{"pass": true, "reason": "ok"}'])
    for c in h.cases:
        docs = load_documents(c.documents)
        assert [d.name for d in docs] == names
        out = render_user(h.prompt.template, c.input, docs)
        assert all(n in out for n in names)
        assert not re.search(r"\d_invoice-", out)
    case = _case(h, "largest-invoice")
    docs = load_documents(case.documents)
    asyncio.run(run_judge(fake, Provider(name="p", base_url="http://x", api_key_env="K"), "m",
                          case.expectation.judge_prompt, case.input, "1043", docs))
    judge_text = json.dumps(fake.calls[0]["messages"])
    assert all(n in judge_text for n in names)
    assert not re.search(r"\d_invoice-", judge_text)
