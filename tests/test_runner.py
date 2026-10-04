import asyncio

import pytest
from conftest import FakeClient

from promptharness.core.client import ChatResult, ClientError
from promptharness.core.models import (
    Case,
    Expectation,
    Harness,
    Match,
    ModelRef,
    PromptVersion,
    Provider,
)
from promptharness.core.runner import RunSettings, evaluate_case, run_harness, run_matrix

PROV = Provider(name="p", base_url="http://x", api_key_env="K")
PROMPT = PromptVersion(system="sys", template="Say: {{ input }}")


def case(name="c", input="hi", **exp):
    return Case(name=name, input=input, expectation=Expectation(**exp))


async def ev(c, script, judge=None, prompt=PROMPT):
    client = FakeClient(script)
    r = await evaluate_case(c, prompt, PROV, "m", client, judge)
    return r, client


async def test_pass_when_checks_pass():
    r, _ = await ev(case(must_include=[Match(pattern="hi")]), ["hi there"])
    assert r.status == "pass" and r.output == "hi there"


async def test_fail_when_must_include_missing():
    r, _ = await ev(case(must_include=[Match(pattern="zzz")]), ["hi there"])
    assert r.status == "fail"


async def test_manual_when_no_expectations():
    r, _ = await ev(case(), ["whatever"])
    assert r.status == "manual"


async def test_system_omitted_if_empty():
    r, c = await ev(case(), ["x"], prompt=PromptVersion(template="{{ input }}"))
    assert [m["role"] for m in c.calls[0]["messages"]] == ["user"]
    r, c = await ev(case(), ["x"])
    assert [m["role"] for m in c.calls[0]["messages"]] == ["system", "user"]


async def test_template_error_is_case_error_and_others_continue():
    h = Harness(
        name="h",
        prompt=PromptVersion(template="{{ nope.x }}"),
        cases=[case("a"), case("b")],
    )
    client = FakeClient([])
    run = await run_harness(h, ModelRef(provider="p", model="m"), {"p": PROV}, client)
    assert [r.status for r in run.results] == ["error", "error"]
    assert client.calls == []
    assert all("nope" in r.error for r in run.results)
    h2 = Harness(
        name="h",
        prompt=PROMPT,
        cases=[Case(name="a", input="x", documents=["/nonexistent/zz"]), case("b")],
    )
    run = await run_harness(h2, ModelRef(provider="p", model="m"), {"p": PROV}, FakeClient(["ok"]))
    assert [r.status for r in run.results] == ["error", "manual"]


async def test_document_error_is_case_error(tmp_path):
    f = tmp_path / "b.bin"
    f.write_bytes(b"\x00\x01")
    c = Case(name="c", documents=[str(f)])
    r, _ = await ev(c, ["x"])
    assert r.status == "error" and "binary" in r.error


@pytest.mark.parametrize("kind", ["auth", "timeout", "rate_limit", "config", "other"])
async def test_client_errors_become_case_errors(kind):
    r, _ = await ev(case(), [ClientError(kind, "boom")])
    assert r.status == "error" and r.error == f"{kind}: boom"


async def test_unexpected_exception_is_error():
    r, _ = await ev(case(), [RuntimeError("bad")])
    assert r.status == "error" and r.error == "RuntimeError: bad"


async def test_missing_provider_errors_all_cases():
    h = Harness(name="h", prompt=PROMPT, cases=[case("a"), case("b")])
    client = FakeClient([])
    run = await run_harness(h, ModelRef(provider="nope", model="m"), {}, client)
    assert [r.status for r in run.results] == ["error", "error"]
    assert all(r.error.startswith("config: provider 'nope' is unknown") for r in run.results)
    off = PROV.model_copy(update={"enabled": False})
    run = await run_harness(h, ModelRef(provider="p", model="m"), {"p": off}, client)
    assert [r.status for r in run.results] == ["error", "error"]
    assert all("is disabled" in r.error for r in run.results)
    assert client.calls == []


async def test_judge_skipped_after_deterministic_failure():
    c = case(must_include=[Match(pattern="zzz")], judge_prompt="good?")
    r, client = await ev(c, ["hi"], judge=(PROV, "j"))
    assert r.status == "fail" and len(client.calls) == 1


async def test_judge_skipped_when_none_or_no_prompt():
    r, client = await ev(case(judge_prompt="g"), ["hi"], judge=None)
    assert len(client.calls) == 1 and r.status == "manual"
    r, client = await ev(case(), ["hi"], judge=(PROV, "j"))
    assert len(client.calls) == 1


async def test_judge_runs_and_passes():
    c = case(judge_prompt="good?")
    r, client = await ev(c, ["hi", '{"pass": true, "reason": "ok"}'], judge=(PROV, "j"))
    assert r.status == "pass" and r.checks[-1].name == "judge"
    assert client.calls[1]["model"] == "j"


async def test_judge_error_status_on_malformed_judge():
    c = case(judge_prompt="good?")
    r, _ = await ev(c, ["hi", "garbage", "garbage"], judge=(PROV, "j"))
    assert r.status == "judge_error" and r.error is None
    assert any("malformed" in w for w in r.warnings)


async def test_judge_client_error_is_case_error():
    c = case(judge_prompt="good?")
    r, _ = await ev(c, ["hi", ClientError("auth", "no")], judge=(PROV, "j"))
    assert r.status == "error" and r.error == "auth: judge request failed: no"


async def test_concurrency_limit_respected():
    state = {"cur": 0, "max": 0}

    async def slow(provider, model, messages, params):
        state["cur"] += 1
        state["max"] = max(state["max"], state["cur"])
        await asyncio.sleep(0.02)
        state["cur"] -= 1
        return "ok"

    h = Harness(name="h", prompt=PROMPT, cases=[case(f"c{i}") for i in range(6)])
    client = FakeClient([slow] * 6)
    await run_harness(
        h, ModelRef(provider="p", model="m"), {"p": PROV}, client, settings=RunSettings(2)
    )
    assert state["max"] == 2


async def test_on_result_called_per_case_as_completed():
    seen = []
    h = Harness(name="h", prompt=PROMPT, cases=[case("a"), case("b"), case("c")])
    run = await run_harness(
        h,
        ModelRef(provider="p", model="m"),
        {"p": PROV},
        FakeClient(["1", "2", "3"]),
        on_result=seen.append,
    )
    assert sorted(r.case_name for r in seen) == ["a", "b", "c"]
    assert [r.case_name for r in run.results] == ["a", "b", "c"]


async def test_results_in_case_order_despite_completion_order():
    async def by_input(provider, model, messages, params):
        n = int(messages[-1]["content"].split(":")[1])
        await asyncio.sleep(0.03 - n * 0.01)
        return str(n)

    h = Harness(
        name="h", prompt=PROMPT, cases=[case(f"c{i}", input=str(i)) for i in range(3)]
    )
    run = await run_harness(
        h,
        ModelRef(provider="p", model="m"),
        {"p": PROV},
        FakeClient([by_input] * 3),
        settings=RunSettings(3),
    )
    assert [r.output for r in run.results] == ["0", "1", "2"]


async def test_only_case_runs_single_case():
    h = Harness(name="h", prompt=PROMPT, cases=[case("a"), case("b")])
    client = FakeClient(["x"])
    run = await run_harness(
        h, ModelRef(provider="p", model="m"), {"p": PROV}, client, only_case="b"
    )
    assert [r.case_name for r in run.results] == ["b"] and len(client.calls) == 1


async def test_results_record_request_tokens_latency():
    res = ChatResult(
        text="out",
        prompt_tokens=3,
        completion_tokens=4,
        latency_ms=55,
        request={"k": 1},
        response={"r": 2},
        warnings=["w"],
    )
    r, _ = await ev(case(), [res])
    assert (r.prompt_tokens, r.completion_tokens, r.latency_ms) == (3, 4, 55)
    assert r.request["k"] == 1 and r.response == {"r": 2} and r.warnings == ["w"]
    assert r.request["documents"] == [] and r.request["messages"][-1]["content"] == "Say: hi"


async def test_run_metadata():
    h = Harness(name="h", prompt=PROMPT, cases=[case("a")])
    run = await run_harness(
        h,
        ModelRef(provider="p", model="m"),
        {"p": PROV},
        FakeClient(["x"]),
        judge_model=ModelRef(provider="p", model="j"),
    )
    assert run.harness == "h" and run.prompt_hash == PROMPT.hash
    assert run.judge_model == ModelRef(provider="p", model="j")
    assert run.started_at and run.finished_at


async def test_run_matrix_returns_run_per_target():
    h = Harness(name="h", prompt=PROMPT, cases=[case("a")])
    targets = [ModelRef(provider="p", model="m1"), ModelRef(provider="p", model="m2")]
    seen = []
    runs = await run_matrix(
        h,
        targets,
        {"p": PROV},
        FakeClient(["x", "y"]),
        None,
        RunSettings(),
        lambda t, r: seen.append((str(t), r.case_name)),
    )
    assert [str(r.model) for r in runs] == ["p:m1", "p:m2"]
    assert seen == [("p:m1", "a"), ("p:m2", "a")]


@pytest.mark.parametrize("enabled,providers_has,word", [(False, True, "disabled"), (True, False, "unknown")])
async def test_unavailable_judge_provider_is_judge_error(enabled, providers_has, word):
    jp = PROV.model_copy(update={"name": "jp", "enabled": enabled})
    provs = {"p": PROV, **({"jp": jp} if providers_has else {})}
    h = Harness(
        name="h",
        prompt=PROMPT,
        cases=[
            case("a", must_include=[Match(pattern="o")], judge_prompt="g"),
            case("b", judge_prompt="g"),
            case("c", must_include=[Match(pattern="zzz")], judge_prompt="g"),
            case("d"),
        ],
    )
    client = FakeClient(["ok"] * 4)
    run = await run_harness(
        h,
        ModelRef(provider="p", model="m"),
        provs,
        client,
        judge_model=ModelRef(provider="jp", model="j"),
    )
    assert [r.status for r in run.results] == ["judge_error", "judge_error", "fail", "manual"]
    assert word in run.results[0].warnings[-1] and "'jp'" in run.results[0].warnings[-1]
    assert run.results[3].warnings == []


async def test_raising_on_result_does_not_lose_results():
    def boom(r):
        raise RuntimeError("cb")

    h = Harness(name="h", prompt=PROMPT, cases=[case("a"), case("b")])
    run = await run_harness(
        h, ModelRef(provider="p", model="m"), {"p": PROV}, FakeClient(["1", "2"]), on_result=boom
    )
    assert [r.output for r in run.results] == ["1", "2"]
    assert all("on_result failed: RuntimeError: cb" in r.warnings for r in run.results)


async def test_unknown_only_case_raises():
    h = Harness(name="h", prompt=PROMPT, cases=[case("a")])
    client = FakeClient([])
    with pytest.raises(ValueError, match="unknown case 'zz'"):
        await run_harness(h, ModelRef(provider="p", model="m"), {"p": PROV}, client, only_case="zz")
    assert client.calls == []


@pytest.mark.asyncio
async def test_runner_uses_the_configured_name(tmp_path):
    f = tmp_path / "d.txt"
    f.write_text("DOCBODY")
    c = Case(name="c", input="hi", documents=[str(f)])
    ok = PromptVersion(template="{{ doc[0].text }}", documents_name="doc")
    r, client = await ev(c, ["out"], prompt=ok)
    assert "DOCBODY" in client.calls[0]["messages"][-1]["content"]
    bad = PromptVersion(template="{{ documents[0].text }}", documents_name="doc")
    r, _ = await ev(c, ["out"], prompt=bad)
    assert r.status == "error"
    assert "'documents' is undefined" in r.error


PNG = b"\x89PNG\r\n\x1a\n" + b"\x05" * 300


def _png(tmp_path, name="pic.png"):
    p = tmp_path / name
    p.write_bytes(PNG)
    return str(p)


def _no_b64_payload(text):
    import base64
    import re

    assert base64.b64encode(PNG).decode()[:40] not in text
    for m in re.finditer(r"base64,([^\"'\\]*)", text):
        assert m.group(1).startswith("<") and m.group(1).endswith("bytes omitted>"), m.group(0)


async def test_image_case_sends_content_parts_and_stores_redacted_request(tmp_path):
    import base64

    seen = {}

    def capture(provider, model, messages, params):
        seen["messages"] = messages
        return "ok"

    c = Case(name="c", input="hi", documents=[_png(tmp_path)])
    r, _ = await ev(c, [capture])
    content = seen["messages"][-1]["content"]
    assert content[0] == {"type": "text", "text": "Say: hi"}
    url = content[1]["image_url"]["url"]
    assert base64.b64decode(url.split(",", 1)[1]) == PNG
    _no_b64_payload(repr(r.request))
    assert r.request["messages"][-1]["content"][1]["image_url"]["url"] == (
        f"data:image/png;base64,<{len(PNG)} bytes omitted>"
    )
    assert r.request["documents"] == [
        {"name": "pic.png", "kind": "image", "mime": "image/png", "bytes": len(PNG)}
    ]


async def test_plain_case_request_is_unchanged(tmp_path):
    f = tmp_path / "d.txt"
    f.write_text("DOC")
    prompt = PromptVersion(system="sys", template="{{ input }} {{ documents[0].text }}")
    r, client = await ev(Case(name="c", input="hi", documents=[str(f)]), ["ok"], prompt=prompt)
    sent = client.calls[0]["messages"]
    assert sent == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi DOC"},
    ]
    assert isinstance(sent[-1]["content"], str)
    assert r.request["messages"] == sent
    assert r.request["documents"] == [
        {"name": "d.txt", "kind": "text", "mime": "text/plain", "chars": 3}
    ]


async def test_document_warnings_reach_the_result(tmp_path):
    import docfixtures

    f = tmp_path / "inv.pdf"
    f.write_bytes(docfixtures.make_pdf(["Alpha", ""]))
    r, _ = await ev(Case(name="c", input="hi", documents=[str(f)]), ["ok"])
    assert "inv.pdf: page 2 has no extractable text" in r.warnings


async def test_one_bad_document_does_not_stop_other_cases(tmp_path):
    bad = tmp_path / "x.dat"
    bad.write_bytes(b"\x00\x01\x02")
    h = Harness(
        name="h",
        prompt=PROMPT,
        cases=[
            Case(name="a", input="x", documents=[str(bad)]),
            case("b", must_include=[Match(pattern="ok")]),
        ],
    )
    client = FakeClient(["ok"])
    run = await run_harness(h, ModelRef(provider="p", model="m"), {"p": PROV}, client)
    assert [r.status for r in run.results] == ["error", "pass"]
    assert "binary" in run.results[0].error
    assert len(client.calls) == 1


async def test_non_vision_hint(tmp_path):
    c = Case(name="c", input="hi", documents=[_png(tmp_path)])
    r, _ = await ev(c, [ClientError("other", "400 bad request")])
    assert r.status == "error"
    assert r.error == "other: 400 bad request (the model may not support image input)"
    r, _ = await ev(case(), [ClientError("other", "400 bad request")])
    assert r.error == "other: 400 bad request"
    r, _ = await ev(c, [ClientError("auth", "no key")])
    assert r.error == "auth: no key"


async def test_non_vision_judge_hint(tmp_path):
    c = Case(
        name="c",
        input="hi",
        documents=[_png(tmp_path)],
        expectation=Expectation(judge_prompt="good?"),
    )
    r, _ = await ev(c, ["ok", ClientError("other", "400 bad request")], judge=(PROV, "j"))
    assert r.status == "error"
    assert r.error == (
        "other: judge request failed: 400 bad request (the model may not support image input)"
    )
    r, _ = await ev(
        case(judge_prompt="good?"), ["ok", ClientError("other", "400 bad request")],
        judge=(PROV, "j"),
    )
    assert r.error == "other: judge request failed: 400 bad request"
    r, _ = await ev(c, ["ok", ClientError("auth", "no key")], judge=(PROV, "j"))
    assert r.error == "auth: judge request failed: no key"


async def test_stored_run_has_no_base64(tmp_path):
    from promptharness.core.db import Database

    h = Harness(
        name="h",
        prompt=PROMPT,
        cases=[Case(name="a", input="x", documents=[_png(tmp_path)])],
    )
    run = await run_harness(h, ModelRef(provider="p", model="m"), {"p": PROV}, FakeClient(["ok"]))
    db = Database(tmp_path / "t.db")
    try:
        db.save_run(run)
        rows = db.conn.execute("SELECT * FROM case_results").fetchall()
        rows += db.conn.execute("SELECT * FROM runs").fetchall()
        text = "\n".join(repr(tuple(row)) for row in rows)
    finally:
        db.close()
    assert "bytes omitted" in text
    _no_b64_payload(text)


async def test_judge_receives_the_case_documents(tmp_path):
    f = tmp_path / "d.txt"
    f.write_text("DOCBODY")
    c = Case(
        name="c", input="hi", documents=[str(f)], expectation=Expectation(judge_prompt="good?")
    )
    r, client = await ev(c, ["out", '{"pass": true, "reason": "ok"}'], judge=(PROV, "j"))
    assert r.status == "pass"
    assert "DOCBODY" in client.calls[1]["messages"][-1]["content"]


async def test_judge_template_error_gives_judge_error_status():
    c = case(judge_prompt="{{ nope }}")
    r, client = await ev(c, ["out"], judge=(PROV, "j"))
    assert r.status == "judge_error"
    assert any("judge prompt template error" in w for w in r.warnings)
    assert len(client.calls) == 1


async def test_document_loading_does_not_block_the_event_loop(tmp_path, monkeypatch):
    import time

    from promptharness.core import runner as runner_mod
    from promptharness.core.render import load_documents as real_load

    state = {"ticks": 0, "ticks_seen_by_loader": None}

    def slow_load(paths):
        time.sleep(0.3)
        state["ticks_seen_by_loader"] = state["ticks"]
        return real_load(paths)

    monkeypatch.setattr(runner_mod, "load_documents", slow_load)

    async def ticker():
        for _ in range(100):
            state["ticks"] += 1
            await asyncio.sleep(0.01)

    tick_task = asyncio.create_task(ticker())
    await asyncio.sleep(0)
    r, _ = await ev(case(), ["ok"])
    tick_task.cancel()
    assert r.status == "manual"
    assert state["ticks_seen_by_loader"] >= 10, state
