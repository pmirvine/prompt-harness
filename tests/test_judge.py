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
