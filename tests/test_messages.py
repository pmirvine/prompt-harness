import base64
import copy

from promptharness.core.documents import Document
from promptharness.core.messages import build_user_content, redact_images, summarize_documents

PNG = b"\x89PNG\r\n\x1a\n" + b"\x01" * 40
JPG = b"\xff\xd8\xff" + b"\x02" * 25


def _img(name, data, mime, index):
    return Document(
        name=name, text=f"[attached image: {name}]", kind="image", mime=mime, index=index,
        _data=data,
    )


def _decode(url):
    head, b64 = url.split(",", 1)
    return head, base64.b64decode(b64)


def test_text_only_is_a_plain_string():
    docs = [Document(name="a.txt", text="hello")]
    assert build_user_content("prompt", docs) == "prompt"
    assert build_user_content("prompt", []) == "prompt"


def test_images_become_content_parts_in_order():
    docs = [
        _img("a.png", PNG, "image/png", 0),
        Document(name="b.txt", text="body", index=1),
        _img("c.jpg", JPG, "image/jpeg", 2),
    ]
    content = build_user_content("the prompt", docs)
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "the prompt"}
    assert [p["type"] for p in content[1:]] == ["image_url", "image_url"]
    assert _decode(content[1]["image_url"]["url"]) == ("data:image/png;base64", PNG)
    assert _decode(content[2]["image_url"]["url"]) == ("data:image/jpeg;base64", JPG)


def test_redact_replaces_data_urls_and_keeps_text():
    docs = [_img("a.png", PNG, "image/png", 0)]
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": build_user_content("the prompt", docs)},
    ]
    before = copy.deepcopy(messages)
    red = redact_images(messages)
    assert messages == before  # input not mutated
    assert red[0] == {"role": "system", "content": "sys"}
    assert red[1]["content"][0] == {"type": "text", "text": "the prompt"}
    assert red[1]["content"][1]["image_url"]["url"] == (
        f"data:image/png;base64,<{len(PNG)} bytes omitted>"
    )
    assert base64.b64encode(PNG).decode() not in repr(red)
    assert redact_images(red) == red  # idempotent


def test_summarize_documents_shape():
    docs = [
        Document(name="a.txt", text="hello"),
        Document(name="r.pdf", text="xyz", mime="application/pdf", index=1, pages=3),
        _img("c.png", PNG, "image/png", 2),
    ]
    assert summarize_documents(docs) == [
        {"name": "a.txt", "kind": "text", "mime": "text/plain", "chars": 5},
        {"name": "r.pdf", "kind": "text", "mime": "application/pdf", "chars": 3, "pages": 3},
        {"name": "c.png", "kind": "image", "mime": "image/png", "bytes": len(PNG)},
    ]
