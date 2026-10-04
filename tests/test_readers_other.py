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


# ---- review round 1 ----

import io  # noqa: E402
import time  # noqa: E402
import zipfile  # noqa: E402

_NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"'
)


def _odf_zip(content: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("content.xml", content)
    return buf.getvalue()


def _doc(body: str, prefix: str = "", encoding: str = "UTF-8") -> bytes:
    xml = (
        f'<?xml version="1.0" encoding="{encoding}"?>{prefix}'
        f"<office:document-content {_NS}><office:body>{body}</office:body>"
        "</office:document-content>"
    )
    return xml.encode(encoding)


_ODT_BODY = "<office:text><text:p>&x;</text:p></office:text>"


def test_odf_doctype_with_entity_rejected(tmp_path):
    data = _odf_zip(_doc(_ODT_BODY, '<!DOCTYPE a [<!ENTITY x "y">]>'))
    with pytest.raises(DocumentError, match="unsafe XML"):
        _load(tmp_path, "x.odt", data)


def test_odf_utf16_doctype_rejected(tmp_path):
    data = _odf_zip(_doc(_ODT_BODY, '<!DOCTYPE a [<!ENTITY x "y">]>', "UTF-16"))
    with pytest.raises(DocumentError, match="unsafe XML"):
        _load(tmp_path, "x.odt", data)


def test_odf_utf16_without_doctype_still_parses(tmp_path):
    data = _odf_zip(_doc("<office:text><text:p>héllo</text:p></office:text>", "", "UTF-16"))
    assert _load(tmp_path, "x.odt", data).text == "héllo"


def test_odf_entity_bomb_rejected_quickly(tmp_path):
    prefix = (
        '<!DOCTYPE a [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
        '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]>'
    )
    data = _odf_zip(_doc("<office:text><text:p>&c;</text:p></office:text>", prefix))
    t = time.monotonic()
    with pytest.raises(DocumentError):
        _load(tmp_path, "x.odt", data)
    assert time.monotonic() - t < 1


def test_odf_external_entity_not_read(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET")
    prefix = f'<!DOCTYPE a [<!ENTITY x SYSTEM "file://{secret}">]>'
    data = _odf_zip(_doc(_ODT_BODY, prefix))
    with pytest.raises(DocumentError) as e:
        _load(tmp_path, "x.odt", data)
    assert "TOPSECRET" not in str(e.value)


def test_odf_malformed_xml(tmp_path):
    with pytest.raises(DocumentError, match="not a valid OpenDocument file"):
        _load(tmp_path, "x.odt", _odf_zip(b"<a><b></a>"))


def test_odf_content_too_large(tmp_path, monkeypatch):
    monkeypatch.setattr(readers_other, "MAX_ODF_XML_BYTES", 100)
    data = _odf_zip(_doc("<office:text><text:p>hello</text:p></office:text>"))
    with pytest.raises(DocumentError, match="content.xml is too large"):
        _load(tmp_path, "x.odt", data)


def _ods(rows_xml: str) -> bytes:
    return _odf_zip(
        _doc(f'<office:spreadsheet><table:table table:name="S">{rows_xml}</table:table>'
             "</office:spreadsheet>")
    )


def _c(v, rep=None):
    r = f' table:number-columns-repeated="{rep}"' if rep else ""
    if v is None:
        return f"<table:table-cell{r}/>"
    return f"<table:table-cell{r}><text:p>{v}</text:p></table:table-cell>"


def _r(cells, rep=None):
    r = f' table:number-rows-repeated="{rep}"' if rep else ""
    return f"<table:table-row{r}>{cells}</table:table-row>"


def test_ods_repeated_cells_and_rows(tmp_path):
    d = _load(tmp_path, "r.ods", _ods(_r(_c("a") + _c("x", 3)) + _r(_c("b"), 2)))
    assert d.text == "--- sheet S ---\na\tx\tx\tx\nb\nb"


def test_ods_trailing_empty_repeats_are_free(tmp_path):
    rows = _r(_c("a") + _c(None, 1048576)) + _r(_c(None, 1048576), 1048576)
    t = time.monotonic()
    d = _load(tmp_path, "r.ods", _ods(rows))
    assert time.monotonic() - t < 1
    assert d.text == "--- sheet S ---\na"


def test_ods_row_cap_with_repeats(tmp_path, monkeypatch):
    monkeypatch.setattr(readers_other, "MAX_SHEET_ROWS", 3)
    d = _load(tmp_path, "r.ods", _ods(_r(_c("a")) + _r(_c("b"), 10)))
    assert d.text.splitlines() == ["--- sheet S ---", "a", "b", "b"]
    assert d.warnings == ("r.ods: sheet 'S' truncated at 3 rows",)


def test_sheet_with_exactly_cap_rows_has_no_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(readers_other, "MAX_SHEET_ROWS", 3)
    d = _load(tmp_path, "s.xlsx", docfixtures.make_xlsx({"S": [[i] for i in range(3)]}))
    assert d.text.splitlines() == ["--- sheet S ---", "0", "1", "2"]
    assert d.warnings == ()


def test_odt_nested_paragraph_text_emitted_once(tmp_path):
    body = (
        "<office:text><text:p>abc<draw:frame><draw:text-box><text:p>BOX</text:p>"
        "</draw:text-box></draw:frame>def</text:p><text:p>last</text:p></office:text>"
    )
    d = _load(tmp_path, "n.odt", _odf_zip(_doc(body)))
    assert d.text.splitlines() == ["abcdef", "BOX", "last"]


def test_xls_bool_error_and_date_cells():
    path = Path(__file__).parent / "fixtures" / "sample.xls"
    d = load_documents([str(path)])[0]
    assert "flag\tTrue" in d.text
    assert "err\t#DIV/0!" in d.text
    assert "when\t2024-03-05 00:00:00" in d.text


def test_doc_converter_runs_with_errors_replace(tmp_path, monkeypatch):
    seen = {}

    def run(cmd, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(readers_other.shutil, "which", _which("antiword"))
    monkeypatch.setattr(readers_other.subprocess, "run", run)
    _load(tmp_path, "a.doc", b"x")
    assert seen["errors"] == "replace" and seen["text"] is True
