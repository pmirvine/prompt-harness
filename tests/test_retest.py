"""Re-test on replacement: core/retest.py."""

from __future__ import annotations

import pytest
from conftest import FakeClient

from promptharness.core.db import Database
from promptharness.core.models import (
    Case,
    Expectation,
    Harness,
    Match,
    ModelRef,
    PromptVersion,
    Provider,
    Run,
)
from promptharness.core.retest import prompt_changed, retest, retest_config
from promptharness.core.runner import RunSettings

OLD = ModelRef.parse("p:old")
NEW = ModelRef.parse("p:new")
JUDGE = ModelRef.parse("p:judge")


def make_harness(name: str = "h", template: str = "Q: {{ input }}") -> Harness:
    return Harness(
        name=name,
        prompt=PromptVersion(template=template),
        cases=[Case(name="c1", input="one",
                    expectation=Expectation(must_include=[Match(pattern="ok")])),
               Case(name="c2", input="two",
                    expectation=Expectation(must_include=[Match(pattern="ok")]))],
    )


def make_run(h: Harness, model: ModelRef = OLD, judge: ModelRef | None = JUDGE) -> Run:
    return Run(harness=h.name, prompt_hash=h.prompt.hash, model=model, judge_model=judge,
               started_at="2026-10-03T00:00:00+00:00")


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "r.db")
    d.save_provider(Provider(name="p", base_url="https://p.test", api_key_env="K"))
    yield d
    d.close()


def test_retest_config_swaps_model_keeps_judge():
    run = make_run(make_harness())
    assert retest_config(run, NEW) == {"harness": "h", "target": NEW, "judge_model": JUDGE}


def test_retest_same_model_raises():
    run = make_run(make_harness())
    with pytest.raises(ValueError):
        retest_config(run, ModelRef.parse("p:old"))


async def test_retest_runs_with_new_model_and_saves(db):
    h = make_harness()
    db.save_harness(h)
    original = make_run(h, judge=None)
    db.save_run(original)
    client = FakeClient(["ok 1", "nope"])
    seen = []
    new = await retest(db, original, NEW, {"p": db.get_provider("p")}, client,
                       RunSettings(concurrency=1), on_result=seen.append)
    assert new.id is not None
    assert new.model == NEW and new.harness == "h" and new.judge_model is None
    assert {c["model"] for c in client.calls} == {"new"}
    assert len(db.list_runs()) == 2
    stored = db.get_run(new.id)
    assert stored.model == NEW
    assert {r.case_name: r.status for r in stored.results} == {"c1": "pass", "c2": "fail"}
    assert len(seen) == 2


async def test_retest_keeps_judge_model(db):
    h = make_harness()
    h.cases[0].expectation.judge_prompt = "is it good?"
    db.save_harness(h)
    original = make_run(h, judge=JUDGE)
    client = FakeClient(["ok", '{"pass": true, "reason": "fine"}', "ok"])
    new = await retest(db, original, NEW, {"p": db.get_provider("p")}, client,
                       RunSettings(concurrency=1))
    assert new.judge_model == JUDGE
    assert "judge" in {c["model"] for c in client.calls}


async def test_retest_same_model_raises_before_running(db):
    h = make_harness()
    db.save_harness(h)
    client = FakeClient([])
    with pytest.raises(ValueError):
        await retest(db, make_run(h), OLD, {}, client, RunSettings())
    assert client.calls == [] and db.list_runs() == []


async def test_retest_missing_harness_raises_clear_error(db):
    run = make_run(make_harness("gone"))
    client = FakeClient([])
    with pytest.raises(ValueError, match="gone"):
        await retest(db, run, NEW, {}, client, RunSettings())
    assert db.list_runs() == []
    assert client.calls == []


async def test_retest_unknown_provider_gives_per_case_errors(db):
    h = make_harness()
    db.save_harness(h)
    client = FakeClient([])
    new = await retest(db, make_run(h), ModelRef.parse("nope:x"), {}, client, RunSettings())
    assert [r.status for r in new.results] == ["error", "error"]
    assert "nope" in new.results[0].error
    assert len(db.list_runs()) == 1
    assert client.calls == []


def test_prompt_changed(db):
    h = make_harness()
    db.save_harness(h)
    run = make_run(h)
    assert prompt_changed(db, run) is False
    db.save_harness(make_harness(template="Changed: {{ input }}"))
    assert prompt_changed(db, run) is True
    assert prompt_changed(db, make_run(make_harness("gone"))) is False
