from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import openai
from openai import AsyncOpenAI

from promptharness.core.models import PromptVersion, Provider

ErrorKind = Literal["auth", "timeout", "rate_limit", "config", "other"]


class ClientError(Exception):
    def __init__(self, kind: ErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind: ErrorKind = kind


@dataclass
class ChatResult:
    text: str
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: int
    request: dict[str, Any]
    response: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


class ChatClient(Protocol):
    async def chat(
        self, provider: Provider, model: str, messages: list[dict], params: PromptVersion | dict
    ) -> ChatResult: ...

    async def list_models(self, provider: Provider) -> list[str]: ...


def _map_error(exc: Exception) -> ClientError:
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return ClientError("auth", str(exc))
    if isinstance(exc, openai.APITimeoutError):
        return ClientError("timeout", str(exc))
    if isinstance(exc, openai.RateLimitError):
        return ClientError("rate_limit", str(exc))
    return ClientError("other", str(exc))


# Sent when a provider has no api_key_env (local servers such as vLLM or Ollama):
# the SDK requires some key, and such servers ignore it.
NO_KEY = "not-needed"


def _make_client(provider: Provider) -> AsyncOpenAI:
    if not provider.api_key_env:
        key = NO_KEY
    else:
        try:
            key = os.environ[provider.api_key_env]
        except KeyError:
            raise ClientError(
                "config", f"environment variable {provider.api_key_env} is not set"
            ) from None
    return AsyncOpenAI(
        base_url=provider.base_url,
        api_key=key,
        default_headers=provider.headers,
        timeout=provider.timeout or 60,
        max_retries=provider.max_retries if provider.max_retries is not None else 2,
    )


def _collect_params(provider: Provider, params: PromptVersion | dict) -> dict[str, Any]:
    if isinstance(params, PromptVersion):
        src: dict[str, Any] = {
            "temperature": params.temperature,
            "max_tokens": params.max_tokens,
            **params.extra_params,
        }
    else:
        src = dict(params)
    out: dict[str, Any] = {}
    for k, v in src.items():
        if v is None:
            continue
        if k in ("max_tokens", "max_completion_tokens"):
            k = provider.max_tokens_param
        out[k] = v
    return out


def _rejected_param(exc: Exception, call_params: dict[str, Any]) -> str | None:
    """Identify which call parameter a 400 error rejects, or None.

    Prefers the SDK's ``param`` attribute; otherwise looks for the key quoted
    ('key' or "key") in the message, so short keys like ``n`` never match by
    accident.
    """
    param = getattr(exc, "param", None)
    if isinstance(param, str) and param in call_params:
        return param
    msg = str(exc)
    for k in call_params:
        if re.search(rf"""['"]{re.escape(k)}['"]""", msg):
            return k
    return None


async def _close(client: AsyncOpenAI) -> None:
    """Release the per-call client's connections; a failed close never masks the result."""
    try:
        await client.close()
    except Exception:
        pass


class OpenAIChatClient:
    async def chat(
        self, provider: Provider, model: str, messages: list[dict], params: PromptVersion | dict
    ) -> ChatResult:
        client = _make_client(provider)
        try:
            return await self._chat(client, provider, model, messages, params)
        finally:
            await _close(client)

    async def _chat(
        self,
        client: AsyncOpenAI,
        provider: Provider,
        model: str,
        messages: list[dict],
        params: PromptVersion | dict,
    ) -> ChatResult:
        call_params = _collect_params(provider, params)
        warnings: list[str] = []
        start = time.perf_counter()
        dropped: set[str] = set()
        while True:
            try:
                resp = await client.chat.completions.create(
                    model=model, messages=messages, **call_params
                )
                break
            except openai.BadRequestError as exc:
                culprit = _rejected_param(exc, call_params)
                if culprit is None or culprit in dropped:
                    raise _map_error(exc) from exc
                dropped.add(culprit)
                del call_params[culprit]
                warnings.append(f"dropped param: {culprit}")
            except ClientError:
                raise
            except Exception as exc:
                raise _map_error(exc) from exc
        latency_ms = int((time.perf_counter() - start) * 1000)

        content = resp.choices[0].message.content if resp.choices else None
        usage = getattr(resp, "usage", None)
        try:
            response = resp.model_dump()
        except Exception:
            response = {}
        return ChatResult(
            text=content or "",
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
            latency_ms=latency_ms,
            request={
                "base_url": provider.base_url,
                "model": model,
                "messages": messages,
                "params": call_params,
            },
            response=response,
            warnings=warnings,
        )

    async def list_models(self, provider: Provider) -> list[str]:
        client = _make_client(provider)
        try:
            page = await client.models.list()
        except Exception as exc:
            raise _map_error(exc) from exc
        finally:
            await _close(client)
        return sorted(m.id for m in page.data)
