from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from promptharness.core import db as dbmod
from promptharness.core.db import MIGRATIONS, Database
from promptharness.core.models import (
    Case,
    CaseResult,
    CheckResult,
    Expectation,
    Harness,
    Match,
    ModelRef,
    PromptVersion,
    Provider,
    Run,
)
from promptharness.core.status import final_status


@pytest.fixture
def db(tmp_path: Path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


def _harness(cases=None) -> Harness:
    return Harness(
        name="h",
        description="d",
        prompt=PromptVersion(system="s", template="t {{input}}", temperature=0.2),
        cases=cases
        or [
            Case(
                name="a",
                input="x",
                documents=["doc1", "doc2"],
                notes="n",
                expectation=Expectation(
                    must_include=[Match(pattern="hi", regex=True)],
                    json_schema={"type": "object"},
                    judge_prompt="ok?",
                ),
            ),
            Case(name="b"),
        ],
    )


def test_fresh_db_has_latest_schema_version(db):
    assert db.schema_version() == len(MIGRATIONS)


def test_reopen_does_not_remigrate(tmp_path):
    p = tmp_path / "t.db"
    d = Database(p)
    d.save_provider(Provider(name="p", base_url="u", api_key_env="K"))
    d.close()
    d = Database(p)
    assert d.schema_version() == len(MIGRATIONS)
    assert d.get_provider("p") is not None
    d.close()


def test_migration_from_older_version(tmp_path, monkeypatch):
    p = tmp_path / "t.db"
    d = Database(p)
    old = d.schema_version()
    d.close()
    monkeypatch.setattr(
        dbmod, "MIGRATIONS", [*MIGRATIONS, "ALTER TABLE harnesses ADD COLUMN x TEXT;"]
    )
    d = Database(p)
    assert d.schema_version() == old + 1
    d.conn.execute("SELECT x FROM harnesses")
    d.close()


def test_provider_roundtrip_and_upsert(db):
    p = Provider(
        name="p",
        base_url="u",
        api_key_env="K",
        headers={"a": "b"},
        max_tokens_param="max_completion_tokens",
        enabled=False,
        timeout=3.5,
        max_retries=2,
    )
    db.save_provider(p)
    assert db.get_provider("p") == p
    p2 = p.model_copy(update={"base_url": "u2"})
    db.save_provider(p2)
    assert db.list_providers() == [p2]
    db.delete_provider("p")
    assert db.get_provider("p") is None


def test_provider_stores_env_name_only(db):
    db.save_provider(Provider(name="p", base_url="u", api_key_env="MY_ENV"))
    cols = [r[1] for r in db.conn.execute("PRAGMA table_info(providers)")]
    assert "api_key_env" in cols
    assert not [c for c in cols if "key" in c and c != "api_key_env"]
    assert db.conn.execute("SELECT api_key_env FROM providers").fetchone()[0] == "MY_ENV"


def test_harness_roundtrip(db):
    h = _harness()
    h.accepted_model = ModelRef(provider="p", model="m")
    h.accepted_run_id = 5
    db.save_harness(h)
    assert db.get_harness("h") == h
    assert db.list_harnesses() == [h]
    assert db.get_harness("nope") is None


def test_set_accepted(db):
    db.save_harness(_harness())
    db.set_accepted("h", ModelRef(provider="p", model="m"), 7)
    got = db.get_harness("h")
    assert got.accepted_model == ModelRef(provider="p", model="m")
    assert got.accepted_run_id == 7


def test_save_harness_replaces_cases(db):
    db.save_harness(_harness())
    h2 = _harness([Case(name="c")])
    h2.prompt = PromptVersion(template="new")
    db.save_harness(h2)
    got = db.get_harness("h")
    assert [c.name for c in got.cases] == ["c"]
    assert got.prompt.template == "new"
    assert db.conn.execute("SELECT COUNT(*) FROM prompt_versions").fetchone()[0] == 2
    db.delete_harness("h")
    assert db.get_harness("h") is None
    assert db.conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 0


def _run(harness="h", started="2026-01-01T00:00:00+00:00") -> Run:
    return Run(
        harness=harness,
        prompt_hash="abc",
        model=ModelRef(provider="p", model="m"),
        judge_model=ModelRef(provider="p", model="j"),
        started_at=started,
        finished_at="2026-01-01T00:01:00+00:00",
        results=[
            CaseResult(
                case_name="a",
                status="fail",
                output="out",
                request={"messages": [{"role": "user", "content": "x"}]},
                response={"id": "r"},
                latency_ms=12,
                prompt_tokens=3,
                completion_tokens=4,
                checks=[CheckResult(name="c", passed=False, reason="no")],
                warnings=["w"],
                error=None,
            ),
            CaseResult(case_name="b", status="manual"),
        ],
    )


def test_run_roundtrip_with_results_and_checks(db):
    r = _run()
    rid = db.save_run(r)
    assert r.id == rid
    assert all(x.id for x in r.results)
    got = db.get_run(rid)
    assert got == r
    assert db.get_run(999) is None


def test_list_runs_newest_first_and_filter(db):
    db.save_run(_run(started="2026-01-01T00:00:00+00:00"))
    db.save_run(_run(started="2026-02-01T00:00:00+00:00"))
    db.save_run(_run(harness="other", started="2026-03-01T00:00:00+00:00"))
    assert [r.started_at[:7] for r in db.list_runs("h")] == ["2026-02", "2026-01"]
    assert len(db.list_runs()) == 3
    assert db.list_runs()[0].harness == "other"


def test_set_manual_verdict_updates_status(db):
    r = _run()
    db.save_run(r)
    rid = r.results[1].id
    res = db.set_manual_verdict(rid, True)
    assert res.status == "pass" and res.manual_verdict is True
    assert db.get_run(r.id).results[1].status == "pass"
    assert db.set_manual_verdict(rid, False).status == "fail"
    assert db.set_manual_verdict(rid, None).status == "manual"


def test_failed_migration_rolls_back_and_is_recoverable(tmp_path, monkeypatch):
    p = tmp_path / "t.db"
    d = Database(p)
    old = d.schema_version()
    d.close()
    bad = "CREATE TABLE t1 (a TEXT);\nTHIS IS NOT SQL;"
    monkeypatch.setattr(dbmod, "MIGRATIONS", [*MIGRATIONS, bad])
    with pytest.raises(sqlite3.Error):
        Database(p)
    monkeypatch.setattr(dbmod, "MIGRATIONS", list(MIGRATIONS))
    d = Database(p)
    assert d.schema_version() == old
    assert not d.conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='t1'"
    ).fetchone()
    d.close()
    monkeypatch.setattr(
        dbmod, "MIGRATIONS", [*MIGRATIONS, "CREATE TABLE t1 (a TEXT);"]
    )
    d = Database(p)
    assert d.schema_version() == old + 1
    d.close()


def test_judge_error_resolved_by_manual_verdict(db):
    r = Run(
        harness="h",
        prompt_hash="abc",
        model=ModelRef(provider="p", model="m"),
        started_at="2026-01-01T00:00:00+00:00",
        results=[
            CaseResult(
                case_name="a", status="judge_error", checks=[CheckResult(name="c", passed=True)]
            )
        ],
    )
    db.save_run(r)
    assert db.set_manual_verdict(r.results[0].id, True).status == "pass"
    assert db.get_run(r.id).results[0].status == "pass"
    assert db.set_manual_verdict(r.results[0].id, None).status == "judge_error"


def _c(p):
    return CheckResult(name="c", passed=p)


@pytest.mark.parametrize(
    "checks,error,judge_error,verdict,expected",
    [
        ([], "boom", False, None, "error"),
        ([_c(False)], "boom", True, True, "error"),
        ([_c(True)], None, True, None, "judge_error"),
        ([_c(False)], None, True, None, "fail"),
        ([_c(False)], None, False, None, "fail"),
        ([_c(True)], None, False, False, "fail"),
        ([], None, False, False, "fail"),
        ([_c(False)], None, False, True, "fail"),
        ([_c(False)], None, True, True, "fail"),
        ([_c(True)], None, True, True, "pass"),
        ([], None, True, True, "pass"),
        ([], None, True, None, "judge_error"),
        ([], None, False, True, "pass"),
        ([_c(True), _c(True)], None, False, None, "pass"),
        ([], None, False, None, "manual"),
    ],
)
def test_final_status_table(checks, error, judge_error, verdict, expected):
    assert final_status(checks, error, judge_error, verdict) == expected


def test_models_roundtrip_in_db(db):
    assert db.list_models("p") == []
    db.save_models("p", ["b", "a", "a"])
    db.save_models("q", ["z"])
    assert db.list_models("p") == ["a", "b"]
    db.save_models("p", ["c"])
    assert db.list_models("p") == ["c"]
    assert db.list_models("q") == ["z"]


def test_v1_database_migrates_to_v2(tmp_path):
    path = tmp_path / "v1.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(MIGRATIONS[0])
    conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', '1')")
    conn.commit()
    conn.close()
    d = Database(path)
    try:
        assert d.schema_version() == len(MIGRATIONS) >= 2
        d.save_models("p", ["m"])
        assert d.list_models("p") == ["m"]
    finally:
        d.close()


def test_delete_provider_removes_its_models(db):
    db.save_provider(Provider(name="p", base_url="u", api_key_env="K"))
    db.save_models("p", ["a"])
    db.save_models("q", ["b"])
    db.delete_provider("p")
    assert db.list_models("p") == []
    assert db.list_models("q") == ["b"]


def test_migration_3_adds_column(tmp_path, monkeypatch):
    p = tmp_path / "t.db"
    with monkeypatch.context() as m:
        m.setattr(dbmod, "MIGRATIONS", list(MIGRATIONS[:2]))
        d = Database(p)
        assert d.schema_version() == 2
        d.conn.execute(
            "INSERT INTO prompt_versions(hash, system, template) VALUES('abc','s','t')"
        )
        d.conn.execute("INSERT INTO harnesses(name, prompt_hash) VALUES('h','abc')")
        d.conn.commit()
        d.close()
    d = Database(p)
    assert d.schema_version() == len(MIGRATIONS)
    assert len(MIGRATIONS) >= 3
    h = d.get_harness("h")
    assert h.prompt.documents_name == "documents"
    d.close()


def test_harness_roundtrip_keeps_documents_name(db):
    h = _harness()
    h.prompt = h.prompt.model_copy(update={"documents_name": "doc"})
    db.save_harness(h)
    assert db.get_harness("h") == h
    h2 = _harness()
    h2.name = "h2"
    db.save_harness(h2)
    n = db.conn.execute("SELECT COUNT(*) FROM prompt_versions").fetchone()[0]
    assert n == 2
    assert db.get_harness("h2").prompt.documents_name == "documents"
