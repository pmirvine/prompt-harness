import pytest

from promptharness.core import documents
from promptharness.core.documents import (
    READERS,
    DocumentError,
    ReadError,
    ReadResult,
    load_documents,
    read_document,
    register,
)
from promptharness.core.render import TemplateRenderError, render_user

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
GIF = b"GIF89a" + b"\x00" * 16
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 8


def test_text_file_becomes_text_document(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("hello")
    d = load_documents([str(p)])[0]
    assert (d.kind, d.mime, d.text, d.name) == ("text", "text/plain", "hello", "a.txt")


def test_indexes_follow_list_order(tmp_path):
    paths = []
    for n in "abc":
        p = tmp_path / f"{n}.txt"
        p.write_text(n)
        paths.append(str(p))
    assert [d.index for d in load_documents(paths)] == [0, 1, 2]


def test_unknown_extension_utf8_text_is_accepted_and_binary_is_not(tmp_path):
    a = tmp_path / "a.log"
    a.write_text("line")
    assert read_document(str(a)).text == "line"
    b = tmp_path / "b.dat"
    b.write_bytes(b"\x00\x01")
    with pytest.raises(DocumentError) as e:
        read_document(str(b))
    assert "unsupported" in str(e.value) and "binary" in str(e.value)


def test_missing_file(tmp_path):
    with pytest.raises(DocumentError) as e:
        read_document(str(tmp_path / "nope.txt"))
    assert "unsupported" in str(e.value) and "cannot read" in str(e.value)


@pytest.mark.parametrize(
    "ext,data,mime",
    [
        (".png", PNG, "image/png"),
        (".jpg", JPEG, "image/jpeg"),
        (".jpeg", JPEG, "image/jpeg"),
        (".gif", GIF, "image/gif"),
        (".webp", WEBP, "image/webp"),
        (".jpg", PNG, "image/png"),
    ],
)
def test_image_documents(tmp_path, ext, data, mime):
    p = tmp_path / f"pic{ext}"
    p.write_bytes(data)
    d = read_document(str(p))
    assert d.kind == "image"
    assert d.mime == mime
    assert d.text == f"[attached image: pic{ext}]"
    assert d._data == data


def test_file_named_png_that_is_not_an_image_is_rejected(tmp_path):
    p = tmp_path / "x.png"
    p.write_text("just text")
    with pytest.raises(DocumentError, match="not a valid image"):
        read_document(str(p))


@pytest.mark.parametrize("ext", [".bmp", ".tiff", ".svg", ".heic"])
def test_unsupported_image_types(tmp_path, ext):
    p = tmp_path / f"x{ext}"
    p.write_bytes(b"data")
    with pytest.raises(DocumentError, match="unsupported image type"):
        read_document(str(p))


def test_image_over_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(documents, "MAX_IMAGE_BYTES", 10)
    p = tmp_path / "big.png"
    p.write_bytes(PNG)
    with pytest.raises(DocumentError, match="image too large"):
        read_document(str(p))


def test_reader_errors_are_wrapped(tmp_path, monkeypatch):
    monkeypatch.setattr(documents, "READERS", dict(READERS))
    p = tmp_path / "a.zzz"
    p.write_bytes(b"x")

    @register(".zzz")
    def boom(data, name):
        raise ReadError("boom")

    with pytest.raises(DocumentError) as e:
        read_document(str(p))
    assert str(e.value) == f"unsupported: {p}: boom"

    @register(".zzz")
    def bad(data, name):
        raise ValueError("x")

    with pytest.raises(DocumentError) as e:
        read_document(str(p))
    assert "cannot read" in str(e.value) and "ValueError" in str(e.value)

    @register(".zzz")
    def ok(data, name):
        return ReadResult(text="fine", pages=2)

    d = read_document(str(p))
    assert (d.text, d.pages) == ("fine", 2)


def test_template_cannot_read_image_bytes(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(PNG)
    docs = load_documents([str(p)])
    with pytest.raises(TemplateRenderError):
        render_user("{{ documents[0]._data }}", "", docs)
    out = render_user(
        "{{ documents[0].kind }} {{ documents[0].mime }} {{ documents[0].index }}", "", docs
    )
    assert out == "image image/png 0"
