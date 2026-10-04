import pytest

from conftest import FakeClient
from promptharness.core.client import ClientError
from promptharness.core.judge import JudgeError, run_judge
from promptharness.core.models import Provider

P = Provider(name="p", base_url="http://x", api_key_env="K")


async def _run(client):
    return await run_judge(client, P, "m", "Be polite", "hi", "hello")


async def test_judge_pass():
    r = await _run(FakeClient(['{"pass": true, "reason": "ok"}']))
    assert r.name == "judge" and r.passed is True and r.reason == "ok"


async def test_judge_fail():
    r = await _run(FakeClient(['{"pass": false, "reason": "rude"}']))
    assert r.passed is False and r.reason == "rude"


async def test_code_fence_stripped():
    r = await _run(FakeClient(['```json\n{"pass": true, "reason": "ok"}\n```']))
    assert r.passed is True


async def test_malformed_then_valid_retries_once():
    c = FakeClient(["nope", '{"pass": true, "reason": "ok"}'])
    r = await _run(c)
    assert r.passed and len(c.calls) == 2


async def test_malformed_twice_raises_judge_error():
    c = FakeClient(["nope", "still nope"])
    with pytest.raises(JudgeError):
        await _run(c)
    assert len(c.calls) == 2


async def test_pass_must_be_bool():
    c = FakeClient(['{"pass": "yes", "reason": "x"}', '{"pass": 1, "reason": "x"}'])
    with pytest.raises(JudgeError):
        await _run(c)


async def test_judge_uses_temperature_zero():
    c = FakeClient(['{"pass": true, "reason": "ok"}'])
    await _run(c)
    call = c.calls[0]
    assert call["params"]["temperature"] == 0
    assert call["messages"][0]["role"] == "system"
    user = call["messages"][1]["content"]
    assert "Be polite" in user and "hi" in user and "hello" in user


async def test_client_error_propagates():
    c = FakeClient([ClientError("auth", "bad key")])
    with pytest.raises(ClientError):
        await _run(c)
    assert len(c.calls) == 1


async def test_output_surrounding_whitespace_is_trimmed_for_the_judge():
    # Reasoning models often emit leading blank lines; a real judge model hung
    # on "Output:\n\n\nParis" but answered promptly for "Output:\nParis".
    c = FakeClient(['{"pass": true, "reason": "ok"}'])
    await run_judge(c, P, "m", "Be polite", "hi", "\n\n  hello there \n")
    user = c.calls[0]["messages"][1]["content"]
    assert user.endswith("Output:\nhello there")


# --- documents and templated judge prompts ---------------------------------

from promptharness.core.documents import Document  # noqa: E402

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x05" * 50
_OK = '{"pass": true, "reason": "ok"}'


def _docs():
    return [
        Document(name="a.txt", text="ALPHA TEXT", index=0),
        Document(
            name="p.png",
            text="[attached image: p.png]",
            kind="image",
            mime="image/png",
            index=1,
            _data=_PNG,
        ),
    ]


def _text_docs():
    return [
        Document(name="a.txt", text="ALPHA TEXT", index=0),
        Document(name="b.txt", text="BETA TEXT", index=1),
    ]


async def test_documents_section_present_with_names_and_text():
    c = FakeClient([_OK])
    await run_judge(c, P, "m", "Be polite", "hi", "hello", _text_docs())
    user = c.calls[0]["messages"][1]["content"]
    assert "Documents:\n[0] a.txt\nALPHA TEXT\n\n[1] b.txt\nBETA TEXT\n\nOutput:" in user
    order = [user.index(s) for s in ("Criteria:", "Input:", "Documents:", "Output:")]
    assert order == sorted(order)


async def test_no_documents_section_without_documents():
    c = FakeClient([_OK])
    await _run(c)
    user = c.calls[0]["messages"][1]["content"]
    assert user == "Criteria:\nBe polite\n\nInput:\nhi\n\nOutput:\nhello"


async def test_images_are_attached_for_the_judge():
    c = FakeClient([_OK])
    await run_judge(c, P, "m", "Be polite", "hi", "hello", _docs())
    content = c.calls[0]["messages"][1]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text" and "[1] p.png\n[attached image: p.png]" in content[0]["text"]
    assert content[1]["type"] == "image_url"


async def test_judge_prompt_can_reference_documents_by_the_configured_name():
    c = FakeClient([_OK])
    await run_judge(
        c, P, "m", "Check {{ doc[1].name }} against {{ output }}", "hi", "hello",
        _text_docs(), "doc",
    )
    user = c.calls[0]["messages"][1]["content"]
    assert user.startswith("Criteria:\nCheck b.txt against hello\n\nInput:")


async def test_plain_prompt_and_json_braces_render_unchanged():
    c = FakeClient([_OK])
    await run_judge(c, P, "m", 'Reply like {"pass": true}', "hi", "hello")
    assert 'Criteria:\nReply like {"pass": true}\n\n' in c.calls[0]["messages"][1]["content"]


@pytest.mark.parametrize("prompt", ["{{ nope }}", "literal {{ here", "{# no close", "a lone {% here"])
async def test_template_error_is_judge_error(prompt):
    c = FakeClient([_OK])
    with pytest.raises(JudgeError, match="^judge prompt template error"):
        await run_judge(c, P, "m", prompt, "hi", "hello")
    assert c.calls == []


async def test_raw_block_escapes():
    c = FakeClient([_OK])
    await run_judge(c, P, "m", "{% raw %}{{ ok }}{% endraw %}", "hi", "hello")
    assert "Criteria:\n{{ ok }}\n\n" in c.calls[0]["messages"][1]["content"]
