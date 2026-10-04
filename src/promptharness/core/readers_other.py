from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.parsers import expat

from promptharness.core.documents import ReadError, ReadResult, register
from promptharness.core.readers_office import check_ooxml_size

MAX_SHEET_ROWS = 5000
MAX_ODF_XML_BYTES = 50 * 1024 * 1024
MAX_REPEAT = 1000

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
    check_ooxml_size(data, ".xlsx")
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


def _xls_cell(book, cell):
    import xlrd

    t, v = cell.ctype, cell.value
    if t == xlrd.XL_CELL_BOOLEAN:
        return str(bool(v))
    if t == xlrd.XL_CELL_ERROR:
        return xlrd.error_text_from_code.get(v, f"#ERR{v}")
    if t == xlrd.XL_CELL_DATE:
        try:
            return str(xlrd.xldate.xldate_as_datetime(v, book.datemode))
        except (xlrd.xldate.XLDateError, ValueError, OverflowError):
            return _cell_text(v)
    return _cell_text(v)


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
        rows = (
            [_xls_cell(book, sh.cell(r, c)) for c in range(sh.ncols)] for r in range(sh.nrows)
        )
        blocks.append(_sheet_block(sh.name, rows, name, warnings))
    return ReadResult(
        text="\n\n".join(blocks), mime=XLS_MIME, pages=len(blocks), warnings=tuple(warnings)
    )


class _Unsafe(Exception):
    pass


def _reject(*_args):
    raise _Unsafe


def _check_no_dtd(data: bytes) -> None:
    """Reject DOCTYPE/ENTITY using expat, so any encoding (e.g. UTF-16) is handled."""
    p = expat.ParserCreate()
    p.StartDoctypeDeclHandler = _reject
    p.EntityDeclHandler = _reject
    p.ExternalEntityRefHandler = _reject
    p.Parse(data, True)


def _content_root(data: bytes) -> ET.Element:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if z.getinfo("content.xml").file_size > MAX_ODF_XML_BYTES:
                raise ReadError("content.xml is too large")
            xml = z.read("content.xml")
    except (zipfile.BadZipFile, KeyError) as e:
        raise ReadError("not a valid OpenDocument file") from e
    try:
        _check_no_dtd(xml)
        return ET.fromstring(xml)
    except _Unsafe as e:
        raise ReadError("unsafe XML: DTD and entity declarations are not allowed") from e
    except (expat.ExpatError, ET.ParseError) as e:
        raise ReadError("not a valid OpenDocument file") from e


_PARA_TAGS = {_q("text", "p"), _q("text", "h")}


def _para_text(el: ET.Element) -> str:
    """Text of a text:p / text:h, honouring spaces, tabs and line breaks.

    Nested paragraphs (e.g. in a draw:text-box inside this paragraph) are skipped
    here; _paragraphs emits them as their own lines right after the outer paragraph.
    """
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
            elif ch.tag in _PARA_TAGS:
                pass
            else:
                walk(ch)
            if ch.tail:
                out.append(ch.tail)

    walk(el)
    return "".join(out)


def _paragraphs(root: ET.Element) -> list[str]:
    return [_para_text(e) for e in root.iter() if e.tag in _PARA_TAGS]


@register(".odt")
def read_odt(data: bytes, name: str) -> ReadResult:
    root = _content_root(data)
    paras = [p for p in _paragraphs(root) if p.strip()]
    return ReadResult(text="\n".join(paras), mime=ODT_MIME)


def _repeat(el: ET.Element, attr: str) -> int:
    try:
        return max(1, int(el.get(_q("table", attr), "1")))
    except ValueError:
        return 1


def _ods_rows(tbl: ET.Element):
    """Yield rows, expanding repeated cells and rows (each repeat capped at MAX_REPEAT).

    Empty cells and rows are only emitted when something follows them, so inner gaps
    keep later columns and rows in place while trailing empties (often repeated to the
    sheet's full size) cost nothing.
    """
    pending_rows = 0
    for row in tbl.iter(_q("table", "table-row")):
        cells: list[str] = []
        pending_cells = 0
        for cell in row.findall(_q("table", "table-cell")):
            text = "\n".join(_para_text(p) for p in cell.findall(_q("text", "p")))
            n = min(_repeat(cell, "number-columns-repeated"), MAX_REPEAT)
            if not text:
                pending_cells += n
                continue
            cells.extend([""] * pending_cells)
            pending_cells = 0
            cells.extend([text] * n)
        rows = min(_repeat(row, "number-rows-repeated"), MAX_REPEAT)
        if not cells:
            pending_rows += rows
            continue
        for _ in range(pending_rows):
            yield []
        pending_rows = 0
        for _ in range(rows):
            yield cells


@register(".ods")
def read_ods(data: bytes, name: str) -> ReadResult:
    root = _content_root(data)
    warnings: list[str] = []
    blocks = []
    for tbl in root.iter(_q("table", "table")):
        blocks.append(
            _sheet_block(tbl.get(_q("table", "name"), ""), _ods_rows(tbl), name, warnings)
        )
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
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=60)
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
            # A private profile, so concurrent conversions do not fight over one.
            profile = Path(tmp, "lo").as_uri()
            _run([office, f"-env:UserInstallation={profile}", "--headless",
                  "--convert-to", "txt:Text", "--outdir", outdir, src])
            out = os.path.join(outdir, "input.txt")
            if not os.path.exists(out):
                raise ReadError("conversion produced no output")
            with open(out, encoding="utf-8", errors="replace") as f:
                text = f.read()
    return ReadResult(text=text.strip(), mime="application/msword")
