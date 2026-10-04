from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import zipfile
from xml.etree import ElementTree as ET

from promptharness.core.documents import ReadError, ReadResult, register

MAX_SHEET_ROWS = 5000

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLS_MIME = "application/vnd.ms-excel"
ODT_MIME = "application/vnd.oasis.opendocument.text"
ODS_MIME = "application/vnd.oasis.opendocument.spreadsheet"
ODP_MIME = "application/vnd.oasis.opendocument.presentation"

_NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
}


def _q(prefix: str, local: str) -> str:
    return f"{{{_NS[prefix]}}}{local}"


def _cell_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _sheet_block(title: str, rows, name: str, warnings: list[str]) -> str:
    lines = [f"--- sheet {title} ---"]
    for n, row in enumerate(rows):
        if n >= MAX_SHEET_ROWS:
            warnings.append(f"{name}: sheet '{title}' truncated at {MAX_SHEET_ROWS} rows")
            break
        lines.append("\t".join(_cell_text(c) for c in row))
    return "\n".join(lines)


@register(".xlsx")
def read_xlsx(data: bytes, name: str) -> ReadResult:
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise ReadError("not a valid .xlsx file")
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        warnings: list[str] = []
        blocks = [
            _sheet_block(ws.title, ws.iter_rows(values_only=True), name, warnings)
            for ws in wb.worksheets
        ]
        return ReadResult(
            text="\n\n".join(blocks), mime=XLSX_MIME, pages=len(blocks), warnings=tuple(warnings)
        )
    finally:
        wb.close()


@register(".xls")
def read_xls(data: bytes, name: str) -> ReadResult:
    import xlrd

    try:
        book = xlrd.open_workbook(file_contents=data)
    except xlrd.XLRDError as e:
        raise ReadError(f"not a valid .xls file ({e})") from e
    warnings: list[str] = []
    blocks = []
    for sh in book.sheets():
        rows = (sh.row_values(r) for r in range(sh.nrows))
        blocks.append(_sheet_block(sh.name, rows, name, warnings))
    return ReadResult(
        text="\n\n".join(blocks), mime=XLS_MIME, pages=len(blocks), warnings=tuple(warnings)
    )


def _content_root(data: bytes) -> ET.Element:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return ET.fromstring(z.read("content.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise ReadError("not a valid OpenDocument file") from e


def _para_text(el: ET.Element) -> str:
    """Text of a text:p / text:h, honouring spaces, tabs and line breaks."""
    out: list[str] = []

    def walk(e: ET.Element) -> None:
        if e.text:
            out.append(e.text)
        for ch in e:
            if ch.tag == _q("text", "s"):
                out.append(" " * int(ch.get(_q("text", "c"), "1")))
            elif ch.tag == _q("text", "tab"):
                out.append("\t")
            elif ch.tag == _q("text", "line-break"):
                out.append("\n")
            else:
                walk(ch)
            if ch.tail:
                out.append(ch.tail)

    walk(el)
    return "".join(out)


def _paragraphs(root: ET.Element) -> list[str]:
    tags = {_q("text", "p"), _q("text", "h")}
    return [_para_text(e) for e in root.iter() if e.tag in tags]


@register(".odt")
def read_odt(data: bytes, name: str) -> ReadResult:
    root = _content_root(data)
    paras = [p for p in _paragraphs(root) if p.strip()]
    return ReadResult(text="\n".join(paras), mime=ODT_MIME)


@register(".ods")
def read_ods(data: bytes, name: str) -> ReadResult:
    root = _content_root(data)
    warnings: list[str] = []
    blocks = []
    for tbl in root.iter(_q("table", "table")):
        rows = (
            [
                "\n".join(_para_text(p) for p in cell.findall(_q("text", "p")))
                for cell in row.findall(_q("table", "table-cell"))
            ]
            for row in tbl.iter(_q("table", "table-row"))
        )
        blocks.append(_sheet_block(tbl.get(_q("table", "name"), ""), rows, name, warnings))
    return ReadResult(
        text="\n\n".join(blocks), mime=ODS_MIME, pages=len(blocks), warnings=tuple(warnings)
    )


@register(".odp")
def read_odp(data: bytes, name: str) -> ReadResult:
    root = _content_root(data)
    out: list[str] = []
    count = 0
    for n, page in enumerate(root.iter(_q("draw", "page")), 1):
        count = n
        out.append(f"--- slide {n} ---")
        out.extend(p for p in _paragraphs(page) if p.strip())
    return ReadResult(text="\n".join(out), mime=ODP_MIME, pages=count)


@register(".rtf")
def read_rtf(data: bytes, name: str) -> ReadResult:
    from striprtf.striprtf import rtf_to_text

    text = rtf_to_text(data.decode("latin-1"), errors="ignore")
    return ReadResult(text=text.strip(), mime="application/rtf")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired as e:
        raise ReadError("conversion timed out after 60 seconds") from e
    except OSError as e:
        raise ReadError(f"cannot run {os.path.basename(cmd[0])}: {e}") from e
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip() or f"exit code {proc.returncode}"
        raise ReadError(f"{os.path.basename(cmd[0])} failed: {msg}")
    return proc


@register(".doc")
def read_doc(data: bytes, name: str) -> ReadResult:
    antiword = shutil.which("antiword")
    office = None if antiword else (shutil.which("soffice") or shutil.which("libreoffice"))
    if not antiword and not office:
        raise ReadError("legacy .doc needs antiword or LibreOffice; convert the file to .docx")
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "input.doc")
        with open(src, "wb") as f:
            f.write(data)
        if antiword:
            text = _run([antiword, src]).stdout
        else:
            outdir = os.path.join(tmp, "out")
            os.mkdir(outdir)
            _run([office, "--headless", "--convert-to", "txt:Text", "--outdir", outdir, src])
            out = os.path.join(outdir, "input.txt")
            if not os.path.exists(out):
                raise ReadError("conversion produced no output")
            with open(out, encoding="utf-8", errors="replace") as f:
                text = f.read()
    return ReadResult(text=text.strip(), mime="application/msword")
