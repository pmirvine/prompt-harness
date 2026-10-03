import pytest


@pytest.fixture(autouse=True)
def _promptharness_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMPTHARNESS_HOME", str(tmp_path / "ph-home"))


import inspect  # noqa: E402

from promptharness.core.client import ChatResult  # noqa: E402


class FakeClient:
    """Scripted ChatClient. script items: str | Exception | callable (sync or async)."""

    def __init__(self, script, models=None):
        self.script = list(script)
        self.models = models if models is not None else []
        self.calls: list[dict] = []
        self._i = 0

    async def chat(self, provider, model, messages, params):
        self.calls.append(
            {"provider": provider, "model": model, "messages": messages, "params": params}
        )
        if self._i >= len(self.script):
            raise AssertionError("FakeClient script exhausted")
        item = self.script[self._i]
        self._i += 1
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(provider, model, messages, params)
            if inspect.isawaitable(item):
                item = await item
        if isinstance(item, ChatResult):
            return item
        return ChatResult(
            text=item,
            prompt_tokens=None,
            completion_tokens=None,
            latency_ms=0,
            request={"model": model, "messages": messages, "params": params},
            response={},
        )

    async def list_models(self, provider):
        if isinstance(self.models, Exception):
            raise self.models
        return list(self.models)
