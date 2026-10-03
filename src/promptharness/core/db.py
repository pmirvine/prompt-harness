from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from promptharness.core.models import (
    Case,
    CaseResult,
    CheckResult,
    Expectation,
    Harness,
    ModelRef,
    PromptVersion,
    Provider,
    Run,
)
from promptharness.core.status import final_status

MIGRATIONS: list[str] = [
    """
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE providers (
        name TEXT PRIMARY KEY,
        base_url TEXT NOT NULL,
        api_key_env TEXT NOT NULL,
        headers TEXT NOT NULL DEFAULT '{}',
        max_tokens_param TEXT NOT NULL DEFAULT 'max_tokens',
        enabled INTEGER NOT NULL DEFAULT 1,
        timeout REAL,
        max_retries INTEGER
    );
    CREATE TABLE prompt_versions (
        hash TEXT PRIMARY KEY,
        system TEXT NOT NULL,
        template TEXT NOT NULL,
        temperature REAL,
        max_tokens INTEGER,
        extra_params TEXT NOT NULL DEFAULT '{}'
    );
    CREATE TABLE harnesses (
        name TEXT PRIMARY KEY,
        description TEXT NOT NULL DEFAULT '',
        prompt_hash TEXT NOT NULL REFERENCES prompt_versions(hash),
        accepted_provider TEXT,
        accepted_model TEXT,
        accepted_run_id INTEGER
    );
    CREATE TABLE cases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        harness TEXT NOT NULL REFERENCES harnesses(name) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        name TEXT NOT NULL,
        input TEXT NOT NULL DEFAULT '',
        documents TEXT NOT NULL DEFAULT '[]',
        notes TEXT NOT NULL DEFAULT '',
        expectation TEXT NOT NULL DEFAULT '{}',
        UNIQUE (harness, name)
    );
    CREATE TABLE runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        harness TEXT NOT NULL,
        prompt_hash TEXT NOT NULL,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        judge_provider TEXT,
        judge_model TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT
    );
    CREATE INDEX idx_runs_harness ON runs(harness, started_at);
    CREATE TABLE case_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        case_name TEXT NOT NULL,
        status TEXT NOT NULL,
        output TEXT NOT NULL DEFAULT '',
        request TEXT NOT NULL DEFAULT '{}',
        response TEXT NOT NULL DEFAULT '{}',
        latency_ms INTEGER,
        prompt_tokens INTEGER,
        completion_tokens INTEGER,
        checks TEXT NOT NULL DEFAULT '[]',
        warnings TEXT NOT NULL DEFAULT '[]',
        error TEXT,
        judge_error INTEGER NOT NULL DEFAULT 0,
        manual_verdict INTEGER
    );
    """,
]


def _j(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def _opt_bool(v) -> bool | None:
    return None if v is None else bool(v)


def _opt_int(v: bool | None) -> int | None:
    return None if v is None else int(v)


def _ref(provider, model) -> ModelRef | None:
    return ModelRef(provider=provider, model=model) if provider is not None else None


class Database:
    def __init__(self, path: Path) -> None:
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    def close(self) -> None:
        self.conn.close()

    # -- migrations ---------------------------------------------------
    def schema_version(self) -> int:
        try:
            row = self.conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
        except sqlite3.OperationalError:
            return 0
        return int(row[0]) if row else 0

    def migrate(self) -> None:
        version = self.schema_version()
        for i in range(version, len(MIGRATIONS)):
            script = (
                "BEGIN;\n"
                + MIGRATIONS[i]
                + "\nINSERT INTO meta(key, value) VALUES('schema_version', '"
                + str(i + 1)
                + "') ON CONFLICT(key) DO UPDATE SET value=excluded.value;\nCOMMIT;"
            )
            try:
                self.conn.executescript(script)
            except Exception:
                if self.conn.in_transaction:
                    self.conn.rollback()
                raise

    # -- providers ----------------------------------------------------
    def save_provider(self, p: Provider) -> None:
        self.conn.execute(
            "INSERT INTO providers(name, base_url, api_key_env, headers, max_tokens_param,"
            " enabled, timeout, max_retries) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET base_url=excluded.base_url,"
            " api_key_env=excluded.api_key_env, headers=excluded.headers,"
            " max_tokens_param=excluded.max_tokens_param, enabled=excluded.enabled,"
            " timeout=excluded.timeout, max_retries=excluded.max_retries",
            (
                p.name,
                p.base_url,
                p.api_key_env,
                _j(p.headers),
                p.max_tokens_param,
                int(p.enabled),
                p.timeout,
                p.max_retries,
            ),
        )
        self.conn.commit()

    @staticmethod
    def _provider(r: sqlite3.Row) -> Provider:
        return Provider(
            name=r["name"],
            base_url=r["base_url"],
            api_key_env=r["api_key_env"],
            headers=json.loads(r["headers"]),
            max_tokens_param=r["max_tokens_param"],
            enabled=bool(r["enabled"]),
            timeout=r["timeout"],
            max_retries=r["max_retries"],
        )

    def get_provider(self, name: str) -> Provider | None:
        r = self.conn.execute("SELECT * FROM providers WHERE name=?", (name,)).fetchone()
        return self._provider(r) if r else None

    def list_providers(self) -> list[Provider]:
        rows = self.conn.execute("SELECT * FROM providers ORDER BY name").fetchall()
        return [self._provider(r) for r in rows]

    def delete_provider(self, name: str) -> None:
        self.conn.execute("DELETE FROM providers WHERE name=?", (name,))
        self.conn.commit()

    # -- harnesses ----------------------------------------------------
    def save_harness(self, h: Harness) -> None:
        pv = h.prompt
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO prompt_versions(hash, system, template, temperature,"
                " max_tokens, extra_params) VALUES(?,?,?,?,?,?)",
                (pv.hash, pv.system, pv.template, pv.temperature, pv.max_tokens,
                 _j(pv.extra_params)),
            )
            am = h.accepted_model
            self.conn.execute(
                "INSERT INTO harnesses(name, description, prompt_hash, accepted_provider,"
                " accepted_model, accepted_run_id) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET description=excluded.description,"
                " prompt_hash=excluded.prompt_hash,"
                " accepted_provider=excluded.accepted_provider,"
                " accepted_model=excluded.accepted_model,"
                " accepted_run_id=excluded.accepted_run_id",
                (h.name, h.description, pv.hash, am.provider if am else None,
                 am.model if am else None, h.accepted_run_id),
            )
            self.conn.execute("DELETE FROM cases WHERE harness=?", (h.name,))
            for i, c in enumerate(h.cases):
                self.conn.execute(
                    "INSERT INTO cases(harness, position, name, input, documents, notes,"
                    " expectation) VALUES(?,?,?,?,?,?,?)",
                    (h.name, i, c.name, c.input, _j(c.documents), c.notes,
                     _j(c.expectation.model_dump(mode="json"))),
                )

    def _harness(self, r: sqlite3.Row) -> Harness:
        pv = self.conn.execute(
            "SELECT * FROM prompt_versions WHERE hash=?", (r["prompt_hash"],)
        ).fetchone()
        cases = self.conn.execute(
            "SELECT * FROM cases WHERE harness=? ORDER BY position", (r["name"],)
        ).fetchall()
        return Harness(
            name=r["name"],
            description=r["description"],
            prompt=PromptVersion(
                system=pv["system"],
                template=pv["template"],
                temperature=pv["temperature"],
                max_tokens=pv["max_tokens"],
                extra_params=json.loads(pv["extra_params"]),
            ),
            cases=[
                Case(
                    name=c["name"],
                    input=c["input"],
                    documents=json.loads(c["documents"]),
                    notes=c["notes"],
                    expectation=Expectation.model_validate(json.loads(c["expectation"])),
                )
                for c in cases
            ],
            accepted_model=_ref(r["accepted_provider"], r["accepted_model"]),
            accepted_run_id=r["accepted_run_id"],
        )

    def get_harness(self, name: str) -> Harness | None:
        r = self.conn.execute("SELECT * FROM harnesses WHERE name=?", (name,)).fetchone()
        return self._harness(r) if r else None

    def list_harnesses(self) -> list[Harness]:
        rows = self.conn.execute("SELECT * FROM harnesses ORDER BY name").fetchall()
        return [self._harness(r) for r in rows]

    def delete_harness(self, name: str) -> None:
        self.conn.execute("DELETE FROM harnesses WHERE name=?", (name,))
        self.conn.commit()

    def set_accepted(self, name: str, model: ModelRef, run_id: int) -> None:
        self.conn.execute(
            "UPDATE harnesses SET accepted_provider=?, accepted_model=?, accepted_run_id=? "
            "WHERE name=?",
            (model.provider, model.model, run_id, name),
        )
        self.conn.commit()

    # -- runs ---------------------------------------------------------
    def save_run(self, run: Run) -> int:
        jm = run.judge_model
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO runs(harness, prompt_hash, provider, model, judge_provider,"
                " judge_model, started_at, finished_at) VALUES(?,?,?,?,?,?,?,?)",
                (run.harness, run.prompt_hash, run.model.provider, run.model.model,
                 jm.provider if jm else None, jm.model if jm else None,
                 run.started_at, run.finished_at),
            )
            run_id = cur.lastrowid
            for i, r in enumerate(run.results):
                c = self.conn.execute(
                    "INSERT INTO case_results(run_id, position, case_name, status, output,"
                    " request, response, latency_ms, prompt_tokens, completion_tokens,"
                    " checks, warnings, error, judge_error, manual_verdict)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, i, r.case_name, r.status, r.output, _j(r.request),
                     _j(r.response), r.latency_ms, r.prompt_tokens, r.completion_tokens,
                     _j([x.model_dump(mode="json") for x in r.checks]), _j(r.warnings),
                     r.error, int(r.status == "judge_error"), _opt_int(r.manual_verdict)),
                )
                r.id = c.lastrowid
        run.id = run_id
        return run_id

    @staticmethod
    def _result(r: sqlite3.Row) -> CaseResult:
        return CaseResult(
            id=r["id"],
            case_name=r["case_name"],
            status=r["status"],
            output=r["output"],
            request=json.loads(r["request"]),
            response=json.loads(r["response"]),
            latency_ms=r["latency_ms"],
            prompt_tokens=r["prompt_tokens"],
            completion_tokens=r["completion_tokens"],
            checks=[CheckResult.model_validate(x) for x in json.loads(r["checks"])],
            warnings=json.loads(r["warnings"]),
            error=r["error"],
            manual_verdict=_opt_bool(r["manual_verdict"]),
        )

    def _run(self, r: sqlite3.Row) -> Run:
        results = self.conn.execute(
            "SELECT * FROM case_results WHERE run_id=? ORDER BY position", (r["id"],)
        ).fetchall()
        return Run(
            id=r["id"],
            harness=r["harness"],
            prompt_hash=r["prompt_hash"],
            model=ModelRef(provider=r["provider"], model=r["model"]),
            judge_model=_ref(r["judge_provider"], r["judge_model"]),
            started_at=r["started_at"],
            finished_at=r["finished_at"],
            results=[self._result(x) for x in results],
        )

    def get_run(self, run_id: int) -> Run | None:
        r = self.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return self._run(r) if r else None

    def list_runs(self, harness: str | None = None) -> list[Run]:
        if harness is None:
            rows = self.conn.execute(
                "SELECT * FROM runs ORDER BY started_at DESC, id DESC"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM runs WHERE harness=? ORDER BY started_at DESC, id DESC",
                (harness,),
            ).fetchall()
        return [self._run(r) for r in rows]

    def set_manual_verdict(self, result_id: int, verdict: bool | None) -> CaseResult:
        row = self.conn.execute(
            "SELECT * FROM case_results WHERE id=?", (result_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no such result: {result_id}")
        res = self._result(row)
        status = final_status(res.checks, res.error, bool(row["judge_error"]), verdict)
        self.conn.execute(
            "UPDATE case_results SET manual_verdict=?, status=? WHERE id=?",
            (_opt_int(verdict), status, result_id),
        )
        self.conn.commit()
        res.manual_verdict = verdict
        res.status = status
        return res
