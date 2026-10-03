from __future__ import annotations

from types import SimpleNamespace

import httpx2 as httpx
import openai
import pytest

from promptharness.core import client as client_mod
from promptharness.core.client import ClientError, OpenAIChatClient
from promptharness.core.models import PromptVersion, Provider

MSGS = [{"role": "user", "content": "hi"}]


def _req() -> httpx.Request:
    return httpx.Request("POST", "http://x/v1/chat/completions")


def _status_err(cls, code: int, msg: str = "boom"):
    return cls(msg, response=httpx.Response(code, request=_req()), body=None)


def _completion(content="hello", usage=(3, 5)):
    msg = SimpleNamespace(content=content)
    u = SimpleNamespace(prompt_tokens=usage[0], completion_tokens=usage[1]) if usage else None
    data = {"choices": [{"message": {"content": content}}]}
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)], usage=u, model_dump=lambda: data
    )


class FakeOpenAI:
    instances: list = []
    script: list = []
    calls: list = []
    models: list = []

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        FakeOpenAI.instances.append(self)

        async def create(**kw):
            FakeOpenAI.calls.append(kw)
            item = FakeOpenAI.script.pop(0) if FakeOpenAI.script else _completion()
            if isinstance(item, Exception):
                raise item
            return item

        async def list_():
            item = FakeOpenAI.models
            if isinstance(item, Exception):
                raise item
            return SimpleNamespace(data=[SimpleNamespace(id=i) for i in item])

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))
        self.models = SimpleNamespace(list=list_)


@pytest.fixture(autouse=True)
def fake(monkeypatch):
    FakeOpenAI.instances, FakeOpenAI.script, FakeOpenAI.calls, FakeOpenAI.models = [], [], [], []
    monkeypatch.setattr(client_mod, "AsyncOpenAI", FakeOpenAI)
    monkeypatch.setenv("TEST_KEY", "sk-secret")


def provider(**kw) -> Provider:
    base = dict(name="p", base_url="http://x/v1", api_key_env="TEST_KEY")
    base.update(kw)
    return Provider(**base)


async def test_chat_returns_text_usage_and_latency():
    pv = PromptVersion(template="t", temperature=0.2, max_tokens=50)
    r = await OpenAIChatClient().chat(provider(), "m", MSGS, pv)
    assert r.text == "hello"
    assert (r.prompt_tokens, r.completion_tokens) == (3, 5)
    assert r.latency_ms >= 0
    assert r.warnings == []
    assert r.request["model"] == "m"
    assert r.request["base_url"] == "http://x/v1"
    assert "sk-secret" not in repr(r.request)
    init = FakeOpenAI.instances[0].init_kwargs
    assert init["api_key"] == "sk-secret"
    assert init["timeout"] == 60
    assert init["max_retries"] == 2


async def test_provider_timeout_and_retries_override():
    await OpenAIChatClient().chat(
        provider(timeout=5, max_retries=0), "m", MSGS, {"temperature": 0}
    )
    init = FakeOpenAI.instances[0].init_kwargs
    assert init["timeout"] == 5
    assert init["max_retries"] == 0


async def test_missing_env_var_is_config_error(monkeypatch):
    monkeypatch.delenv("NOPE_KEY", raising=False)
    with pytest.raises(ClientError) as ei:
        await OpenAIChatClient().chat(provider(api_key_env="NOPE_KEY"), "m", MSGS, {})
    assert ei.value.kind == "config"
    assert "NOPE_KEY" in str(ei.value)


async def test_uses_max_completion_tokens_when_configured():
    pv = PromptVersion(template="t", max_tokens=77)
    await OpenAIChatClient().chat(provider(max_tokens_param="max_completion_tokens"), "m", MSGS, pv)
    kw = FakeOpenAI.calls[0]
    assert kw["max_completion_tokens"] == 77
    assert "max_tokens" not in kw


async def test_none_params_omitted():
    pv = PromptVersion(template="t")
    await OpenAIChatClient().chat(provider(), "m", MSGS, pv)
    kw = FakeOpenAI.calls[0]
    assert "temperature" not in kw
    assert "max_tokens" not in kw


async def test_extra_params_passed():
    pv = PromptVersion(template="t", extra_params={"top_p": 0.5})
    await OpenAIChatClient().chat(provider(), "m", MSGS, pv)
    assert FakeOpenAI.calls[0]["top_p"] == 0.5


@pytest.mark.parametrize("content", ["", None])
async def test_empty_content_becomes_empty_string(content):
    FakeOpenAI.script = [_completion(content)]
    r = await OpenAIChatClient().chat(provider(), "m", MSGS, {})
    assert r.text == ""


@pytest.mark.parametrize(
    "exc,kind",
    [
        (_status_err(openai.AuthenticationError, 401), "auth"),
        (_status_err(openai.PermissionDeniedError, 403), "auth"),
        (_status_err(openai.RateLimitError, 429), "rate_limit"),
        (openai.APITimeoutError(_req()), "timeout"),
        (_status_err(openai.InternalServerError, 500), "other"),
    ],
)
async def test_error_mapping(exc, kind):
    FakeOpenAI.script = [exc]
    with pytest.raises(ClientError) as ei:
        await OpenAIChatClient().chat(provider(), "m", MSGS, {})
    assert ei.value.kind == kind


async def test_unsupported_param_dropped_with_warning():
    FakeOpenAI.script = [
        _status_err(openai.BadRequestError, 400, "Unsupported value: 'temperature' not supported"),
        _completion("ok"),
    ]
    pv = PromptVersion(template="t", temperature=0.7)
    r = await OpenAIChatClient().chat(provider(), "m", MSGS, pv)
    assert r.text == "ok"
    assert r.warnings == ["dropped param: temperature"]
    assert "temperature" in FakeOpenAI.calls[0]
    assert "temperature" not in FakeOpenAI.calls[1]


async def test_unrelated_bad_request_is_other_error():
    FakeOpenAI.script = [_status_err(openai.BadRequestError, 400, "invalid model")]
    with pytest.raises(ClientError) as ei:
        await OpenAIChatClient().chat(provider(), "m", MSGS, PromptVersion(template="t", temperature=1))
    assert ei.value.kind == "other"
    assert len(FakeOpenAI.calls) == 1


async def test_list_models_returns_ids_sorted():
    FakeOpenAI.models = ["b", "a", "c"]
    assert await OpenAIChatClient().list_models(provider()) == ["a", "b", "c"]


async def test_list_models_failure_is_client_error():
    FakeOpenAI.models = _status_err(openai.AuthenticationError, 401)
    with pytest.raises(ClientError) as ei:
        await OpenAIChatClient().list_models(provider())
    assert ei.value.kind == "auth"


def _bad(msg: str, param: str | None = None):
    exc = _status_err(openai.BadRequestError, 400, msg)
    if param is not None:
        exc.param = param
    return exc


async def test_two_params_dropped_in_turn():
    FakeOpenAI.script = [
        _bad("Unsupported value: 'temperature' does not support 0.7"),
        _bad("Unsupported parameter: 'max_tokens' is not supported with this model"),
        _completion("ok"),
    ]
    pv = PromptVersion(template="t", temperature=0.7, max_tokens=10)
    r = await OpenAIChatClient().chat(provider(), "m", MSGS, pv)
    assert r.text == "ok"
    assert r.warnings == ["dropped param: temperature", "dropped param: max_tokens"]
    assert len(FakeOpenAI.calls) == 3
    assert "temperature" not in FakeOpenAI.calls[2]
    assert "max_tokens" not in FakeOpenAI.calls[2]


async def test_param_attribute_preferred():
    FakeOpenAI.script = [_bad("this value is not supported", param="top_p"), _completion("ok")]
    pv = PromptVersion(template="t", temperature=0.7, extra_params={"top_p": 0.5})
    r = await OpenAIChatClient().chat(provider(), "m", MSGS, pv)
    assert r.warnings == ["dropped param: top_p"]
    assert "top_p" not in FakeOpenAI.calls[1]
    assert FakeOpenAI.calls[1]["temperature"] == 0.7


async def test_short_key_not_matched_by_substring():
    FakeOpenAI.script = [_bad("invalid request: unknown model name")]
    pv = PromptVersion(template="t", extra_params={"n": 2})
    with pytest.raises(ClientError) as ei:
        await OpenAIChatClient().chat(provider(), "m", MSGS, pv)
    assert ei.value.kind == "other"
    assert len(FakeOpenAI.calls) == 1


async def test_repeat_rejection_of_dropped_param_raises():
    FakeOpenAI.script = [
        _bad("Unsupported value: 'temperature'"),
        _bad("Unsupported value: 'temperature'"),
    ]
    pv = PromptVersion(template="t", temperature=0.7, max_tokens=10)
    with pytest.raises(ClientError) as ei:
        await OpenAIChatClient().chat(provider(), "m", MSGS, pv)
    assert ei.value.kind == "other"
    assert len(FakeOpenAI.calls) == 2
