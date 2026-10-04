import io

import docfixtures
import pytest
from pypdf import PdfReader, PdfWriter

from promptharness.core.documents import DocumentError, load_documents

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def _load(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return load_documents([str(p)])[0]


def _encrypted(pages, password):
    reader = PdfReader(io.BytesIO(docfixtures.make_pdf(pages)))
    w = PdfWriter()
    for page in reader.pages:
        w.add_page(page)
    w.encrypt(password, algorithm="RC4-128")  # needs no optional dependency
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_make_pdf_round_trips_through_pypdf():
    r = PdfReader(io.BytesIO(docfixtures.make_pdf(["Alpha (1)", "Beta 2"])))
    assert [p.extract_text() for p in r.pages] == ["Alpha (1)", "Beta 2"]


def test_pdf_text_and_pages(tmp_path):
    d = _load(tmp_path, "a.pdf", docfixtures.make_pdf(["Alpha 1", "Beta 2"]))
    assert "--- page 1 ---" in d.text and "Alpha 1" in d.text
    assert "--- page 2 ---" in d.text and "Beta 2" in d.text
    assert d.pages == 2
    assert (d.kind, d.mime) == ("text", "application/pdf")


def test_pdf_blank_page_warns(tmp_path):
    d = _load(tmp_path, "inv.pdf", docfixtures.make_pdf(["Alpha", ""]))
    assert "Alpha" in d.text
    assert d.warnings == ("inv.pdf: page 2 has no extractable text",)


def test_pdf_with_no_text_is_an_error(tmp_path):
    with pytest.raises(DocumentError, match="no extractable text"):
        _load(tmp_path, "scan.pdf", docfixtures.make_pdf(["", ""]))


def test_pdf_not_a_pdf(tmp_path):
    with pytest.raises(DocumentError, match="not a valid PDF"):
        _load(tmp_path, "a.pdf", b"just text")


def test_pdf_corrupt(tmp_path):
    with pytest.raises(DocumentError, match="unsupported"):
        _load(tmp_path, "a.pdf", b"%PDF-1.4\n" + b"junk" * 50)


def test_pdf_encrypted_needs_password(tmp_path):
    with pytest.raises(DocumentError, match="password-protected"):
        _load(tmp_path, "a.pdf", _encrypted(["Secret"], "hunter2"))


def test_pdf_encrypted_with_empty_password_reads(tmp_path):
    d = _load(tmp_path, "a.pdf", _encrypted(["Open sesame"], ""))
    assert "Open sesame" in d.text


def test_pdf_encrypted_without_cryptography(tmp_path, monkeypatch):
    import pypdf
    from pypdf.errors import DependencyError

    def boom(self, *a, **k):
        raise DependencyError("cryptography>=3.1 is required")

    monkeypatch.setattr(pypdf.PdfReader, "decrypt", boom)
    with pytest.raises(DocumentError, match="needs the 'cryptography' package"):
        _load(tmp_path, "a.pdf", _encrypted(["Secret"], "hunter2"))


def test_docx_paragraphs_and_table_in_order(tmp_path):
    data = docfixtures.make_docx(
        [], blocks=["Before table", [["Item", "Price"], ["Widget", "9.00"]], "After table"]
    )
    d = _load(tmp_path, "a.docx", data)
    t = d.text
    assert "Item | Price" in t and "Widget | 9.00" in t
    assert t.index("Before table") < t.index("Item | Price") < t.index("Widget | 9.00")
    assert t.index("Widget | 9.00") < t.index("After table")
    assert d.mime == DOCX_MIME


def test_docx_not_a_zip(tmp_path):
    with pytest.raises(DocumentError, match="not a valid .docx file"):
        _load(tmp_path, "a.docx", b"plain text")


def test_pptx_slides_tables_and_notes(tmp_path):
    data = docfixtures.make_pptx(
        [
            {"title": "Intro", "body": "Welcome"},
            {
                "title": "Pricing",
                "body": "See table",
                "notes": "Mention discount",
                "table": [["Item", "Price"], ["Widget", "9.00"]],
            },
        ]
    )
    d = _load(tmp_path, "a.pptx", data)
    for s in ("--- slide 1 ---", "Intro", "Welcome", "--- slide 2 ---", "Pricing",
              "See table", "Widget | 9.00", "Notes: Mention discount"):
        assert s in d.text
    assert d.text.index("Intro") < d.text.index("--- slide 2 ---") < d.text.index("Pricing")
    assert d.pages == 2
    assert d.mime == PPTX_MIME


def test_pptx_reads_text_inside_group_shapes(tmp_path):
    data = docfixtures.make_pptx([{"title": "T", "body": "B", "group_text": "Grouped words"}])
    assert "Grouped words" in _load(tmp_path, "a.pptx", data).text
