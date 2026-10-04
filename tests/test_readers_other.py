import subprocess
from pathlib import Path

import docfixtures
import pytest

from promptharness.core import readers_other
from promptharness.core.documents import DocumentError, load_documents


def _load(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return load_documents([str(p)])[0]


def test_xlsx_sheets_rows_and_numbers(tmp_path):
    data = docfixtures.make_xlsx(
        {"Data": [["name", "score"], ["a", 9.5], ["b", 3.0]], "Other": [["x", None, "z"]]}
    )
    d = _load(tmp_path, "s.xlsx", data)
    assert "--- sheet Data ---\nname\tscore\na\t9.5\nb\t3" in d.text
    assert "--- sheet Other ---\nx\t\tz" in d.text
    assert d.pages == 2
    assert d.mime == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_xlsx_row_cap_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(readers_other, "MAX_SHEET_ROWS", 3)
    d = _load(tmp_path, "s.xlsx", docfixtures.make_xlsx({"S": [[i] for i in range(5)]}))
    assert d.text.splitlines() == ["--- sheet S ---", "0", "1", "2"]
    assert d.warnings == ("s.xlsx: sheet 'S' truncated at 3 rows",)


def test_xlsx_corrupt(tmp_path):
    with pytest.raises(DocumentError, match="unsupported"):
        _load(tmp_path, "bad.xlsx", b"not a zip")


def test_xls_fixture_reads():
    path = Path(__file__).parent / "fixtures" / "sample.xls"
    d = load_documents([str(path)])[0]
    assert "--- sheet Prices ---" in d.text
    assert "apple\t1.5" in d.text and "pear\t3" in d.text
    assert d.mime == "application/vnd.ms-excel"
    assert d.pages == 1


def test_odt_ods_odp(tmp_path):
    d = _load(tmp_path, "a.odt", docfixtures.make_odt(["# Title", "first", "second"]))
    assert d.text.splitlines() == ["Title", "first", "second"]
    assert d.mime == "application/vnd.oasis.opendocument.text"

    d = _load(tmp_path, "a.ods", docfixtures.make_ods({"Sh": [["a", 2], ["b", 3.5]]}))
    assert d.text == "--- sheet Sh ---\na\t2\nb\t3.5"
    assert d.pages == 1
    assert d.mime == "application/vnd.oasis.opendocument.spreadsheet"

    d = _load(tmp_path, "a.odp", docfixtures.make_odp([["one", "uno"], ["two"]]))
    assert d.text == "--- slide 1 ---\none\nuno\n--- slide 2 ---\ntwo"
    assert d.pages == 2
    assert d.mime == "application/vnd.oasis.opendocument.presentation"


def test_odf_invalid(tmp_path):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("other.xml", "<a/>")
    for data in (buf.getvalue(), b"junk"):
        with pytest.raises(DocumentError, match="not a valid OpenDocument file"):
            _load(tmp_path, "x.odt", data)


def test_rtf(tmp_path):
    rtf = rb"{\rtf1\ansi {\b Hello} world\par Second line\par}"
    d = _load(tmp_path, "a.rtf", rtf)
    assert "Hello world" in d.text and "Second line" in d.text
    assert "\\" not in d.text and "rtf1" not in d.text
    assert d.mime == "application/rtf"


def _which(*present):
    return lambda cmd: f"/bin/{cmd}" if cmd in present else None


def test_doc_uses_antiword_when_present(tmp_path, monkeypatch):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="hello", stderr="")

    monkeypatch.setattr(readers_other.shutil, "which", _which("antiword"))
    monkeypatch.setattr(readers_other.subprocess, "run", run)
    d = _load(tmp_path, "a.doc", b"\xd0\xcf\x11\xe0")
    assert d.text == "hello"
    assert d.mime == "application/msword"
    assert calls[0][0].endswith("antiword")


def test_doc_falls_back_to_soffice(tmp_path, monkeypatch):
    def run(cmd, **kw):
        outdir = Path(cmd[cmd.index("--outdir") + 1])
        (outdir / (Path(cmd[-1]).stem + ".txt")).write_text("converted", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(readers_other.shutil, "which", _which("soffice"))
    monkeypatch.setattr(readers_other.subprocess, "run", run)
    assert _load(tmp_path, "a.doc", b"x").text == "converted"


def test_doc_without_any_converter(tmp_path, monkeypatch):
    monkeypatch.setattr(readers_other.shutil, "which", _which())
    with pytest.raises(DocumentError, match="convert the file to .docx"):
        _load(tmp_path, "a.doc", b"x")


def test_doc_converter_failure_and_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(readers_other.shutil, "which", _which("antiword"))
    monkeypatch.setattr(
        readers_other.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom bad file"),
    )
    with pytest.raises(DocumentError, match="boom bad file"):
        _load(tmp_path, "a.doc", b"x")

    def timeout(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 60)

    monkeypatch.setattr(readers_other.subprocess, "run", timeout)
    with pytest.raises(DocumentError, match="timed out"):
        _load(tmp_path, "a.doc", b"x")
