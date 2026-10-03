# PromptHarness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local Textual TUI plus headless CLI for iterating on prompts and regression-testing saved harnesses across OpenAI-compatible providers.

**Architecture:** A Textual-free `core` package (models, SQLite, client, checks, judge, runner, portable I/O) with the TUI and Typer CLI as thin layers over it. SQLite is the source of truth; YAML/JSON is export/import only. The runner is async with a semaphore and emits one event per finished case.

**Tech Stack:** Python 3.11+, Textual, openai (`AsyncOpenAI`), Jinja2, pydantic v2, Typer, PyYAML, jsonschema, sqlite3, pytest + pytest-asyncio, uv, hatchling.

**Spec:** `docs/superpowers/specs/2026-10-03-promptharness-design.md`

## Global Constraints

- Python 3.11+; install with `uv pip install -e .` (or pip); console script `promptharness`.
- Core package `promptharness/core/` must not import Textual.
- API keys are never stored: providers hold only `api_key_env`; key read from `os.environ` at call time.
- No network calls except to configured provider `base_url`s. Tests use a fake client only.
- Defaults: concurrency 2, retries 2, timeout 60s. Chat completions only. Tokens only, no cost.
- Templates receive `input` (str) and `documents` (list of `{name, text}`).
- Non-text/unreadable documents give a clear "unsupported" error on that case, never a guess.
- Check order: call error → must-include/must-not-include → exact/normalized → JSON/JSON Schema → judge → manual. Deterministic failure skips the judge.
- Statuses: `pass`, `fail`, `error`, `manual`, `judge_error`. Malformed judge output retries once, then `judge_error`.
- Prompt versions are immutable, identified by content hash. Export carries `format_version`, never keys.
- Data dir: platform data dir, overridable with `PROMPTHARNESS_HOME`. SQLite `meta.schema_version` with ordered migrations.
- CLI `run` exits non-zero on any `fail`/`error`/`judge_error`; `manual` does not fail.
- Out of scope: cost tracking, Responses API, cloud sync, accounts, web UI.

## Review Focus

1. Invalid regex in an expectation: becomes a failed check with the reason, not an exception (Task 3).
2. Jinja template syntax error or undefined variable: case `error` with the message, run continues (Task 2, Task 7).
3. Provider returns empty/`None` content: output is `""`, checks still run (Task 5).
4. Missing API-key env var: per-case `error` naming the variable (Task 5, Task 7).
5. Import of a file with unknown/newer `format_version`, or with duplicate case names: rejected with a clear message before touching the DB (Task 8).

---

## File Structure

```
pyproject.toml
README.md
examples/summarize.harness.yaml
src/promptharness/__init__.py
src/promptharness/paths.py          # data dir resolution
src/promptharness/core/{__init__,models,render,checks,client,judge,runner,db,portable}.py
src/promptharness/cli.py
src/promptharness/tui/{__init__,app,providers,studio,harnesses,matrix,compare,runs}.py
src/promptharness/tui/app.tcss
tests/{conftest.py,test_render.py,test_checks.py,test_client.py,test_judge.py,test_runner.py,test_db.py,test_portable.py,test_cli.py,test_tui_smoke.py}
```

---

### Task 1: Scaffold and paths

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `src/promptharness/__init__.py`, `src/promptharness/paths.py`, `src/promptharness/cli.py` (stub), `tests/conftest.py`, `tests/test_paths.py`

**Interfaces:**
- Produces: `paths.home_dir() -> Path` (honours `PROMPTHARNESS_HOME`, else `platformdirs.user_data_dir("promptharness")`, creates it), `paths.db_path() -> Path` (`home_dir()/promptharness.db`); `cli.app: typer.Typer`; console script `promptharness = promptharness.cli:app`.

- [ ] **Step 1: Write failing test** `test_paths.py::test_home_dir_respects_env` — set `PROMPTHARNESS_HOME` to `tmp_path/"x"` via monkeypatch; assert `home_dir() == tmp_path/"x"` and the directory exists; `db_path().name == "promptharness.db"`.
- [ ] **Step 2: Run** `uv run pytest tests/test_paths.py -v` → FAIL (module missing).
- [ ] **Step 3: Create project.** `pyproject.toml` with hatchling, `requires-python = ">=3.11"`, deps `textual openai jinja2 pydantic typer pyyaml jsonschema platformdirs`, dev group `pytest pytest-asyncio`, `asyncio_mode = "auto"`, src layout, script entry. `uv venv && uv pip install -e . --group dev`. Implement `paths.py`. `cli.py` is a Typer app whose default callback prints a placeholder (replaced in Task 9/10). `conftest.py` has an autouse fixture setting `PROMPTHARNESS_HOME` to a tmp dir.
- [ ] **Step 4: Run** `uv run pytest -v` → PASS; `uv run promptharness --help` exits 0.
- [ ] **Step 5: Commit** `feat: scaffold package and paths`.

---

### Task 2: Models and rendering

**Files:**
- Create: `src/promptharness/core/__init__.py`, `core/models.py`, `core/render.py`, `tests/test_render.py`

**Interfaces:**
- Produces (`models.py`, pydantic v2 `BaseModel`s):
  - `ModelRef(provider: str, model: str)` with `ModelRef.parse("prov:model-id") -> ModelRef` (split on first `:`; raises `ValueError` if no colon) and `__str__` → `"prov:model-id"`.
  - `Provider(name, base_url, api_key_env, headers: dict[str,str]={}, max_tokens_param: Literal["max_tokens","max_completion_tokens"]="max_tokens", enabled=True, timeout: float|None=None, max_retries: int|None=None)`.
  - `PromptVersion(system: str="", template: str, temperature: float|None=None, max_tokens: int|None=None, extra_params: dict={})` with property `hash -> str` (first 12 hex of sha256 over canonical JSON of all fields).
  - `Match(pattern: str, regex: bool=False)`.
  - `Expectation(must_include: list[Match]=[], must_not_include: list[Match]=[], exact: str|None=None, normalized: str|None=None, json_output: bool=False, json_schema: dict|None=None, judge_prompt: str|None=None)`.
  - `Case(name: str, input: str="", documents: list[str]=[], notes: str="", expectation: Expectation=Expectation())`.
  - `Harness(name, description="", prompt: PromptVersion, cases: list[Case], accepted_model: ModelRef|None=None, accepted_run_id: int|None=None)` — validator rejects duplicate case names.
  - `CheckResult(name: str, passed: bool, reason: str="")`.
  - `Status = Literal["pass","fail","error","manual","judge_error"]`.
  - `CaseResult(id: int|None=None, case_name: str, status: Status, output: str="", request: dict={}, response: dict={}, latency_ms: int|None=None, prompt_tokens: int|None=None, completion_tokens: int|None=None, checks: list[CheckResult]=[], warnings: list[str]=[], error: str|None=None, manual_verdict: bool|None=None)`.
  - `Run(id: int|None=None, harness: str, prompt_hash: str, model: ModelRef, judge_model: ModelRef|None=None, started_at: str, finished_at: str|None=None, results: list[CaseResult]=[])`.
- Produces (`render.py`): `Document(name: str, text: str)`; `class DocumentError(Exception)`; `load_documents(paths: list[str]) -> list[Document]` (raises `DocumentError("unsupported: <path>: <reason>")` for missing, non-UTF-8, or binary/NUL-containing files); `render_user(template: str, input: str, documents: list[Document]) -> str` using `jinja2.sandbox.SandboxedEnvironment(undefined=StrictUndefined)`, raising `TemplateRenderError(str)` on syntax/undefined errors.

- [ ] **Step 1: Write failing tests** in `test_render.py`:
  - `test_render_input_and_documents`: template `"{{ input }}|{% for d in documents %}{{ d.name }}={{ d.text }};{% endfor %}"` → `"hi|a.txt=A;"`.
  - `test_undefined_variable_raises`: `"{{ nope }}"` → `TemplateRenderError`.
  - `test_syntax_error_raises`: `"{% if %}"` → `TemplateRenderError`.
  - `test_load_documents_text`: tmp file → `Document(name="a.txt", text="A")` (name is basename).
  - `test_load_documents_binary_unsupported`: bytes `b"\x00\x01"` → `DocumentError` containing `"unsupported"`.
  - `test_load_documents_missing`: nonexistent → `DocumentError` containing `"unsupported"`.
  - `test_prompt_hash_stable_and_sensitive`: same fields → same hash; changing `temperature` → different hash.
  - `test_duplicate_case_names_rejected`: `Harness(...)` with two cases named "a" → `ValidationError`.
  - `test_modelref_parse`: `"openai:gpt-4o"` round-trips; `"nocolon"` raises `ValueError`; `"or:meta/llama:free"` parses to model `"meta/llama:free"`.
- [ ] **Step 2: Run** `uv run pytest tests/test_render.py -v` → FAIL.
- [ ] **Step 3: Implement** `models.py` and `render.py` per the signatures above.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: data models and template rendering`.

---

### Task 3: Deterministic checks

**Files:**
- Create: `src/promptharness/core/checks.py`, `tests/test_checks.py`

**Interfaces:**
- Consumes: `Expectation`, `Match`, `CheckResult`.
- Produces: `normalize(text: str) -> str` (strip, collapse whitespace runs to one space, casefold); `run_checks(output: str, exp: Expectation) -> list[CheckResult]` returning one `CheckResult` per configured check, in the order: must_include, must_not_include, exact, normalized, json_output, json_schema. Check names: `include:<pattern>`, `exclude:<pattern>`, `exact`, `normalized`, `json`, `json_schema`.

- [ ] **Step 1: Write failing tests** in `test_checks.py`:
  - `test_include_substring_pass_and_fail` (case-sensitive substring).
  - `test_must_not_include_fails_when_present`.
  - `test_regex_include` with `Match(pattern=r"\d{3}", regex=True)` on `"call 555"` passes, on `"call"` fails.
  - `test_invalid_regex_is_failed_check_not_exception`: `Match(pattern="(", regex=True)` → `passed False`, reason contains `"invalid regex"`.
  - `test_exact_vs_normalized`: output `" Hello   World\n"`: `exact="Hello   World"` fails; `normalized="hello world"` passes.
  - `test_json_output_valid_invalid`: `'{"a":1}'` passes; `"nope"` fails with parse reason; fenced ```` ```json ... ``` ```` is accepted by stripping one surrounding code fence.
  - `test_json_schema`: schema `{"type":"object","required":["a"]}`; `'{"b":1}'` fails with reason naming `a`; `json_schema` set implies json parse (non-JSON output fails `json_schema` with a parse reason).
  - `test_no_expectations_returns_empty_list`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `checks.py`; use `jsonschema.Draft202012Validator` and report the first error message.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: deterministic expectation checks`.

---

### Task 4: SQLite storage

**Files:**
- Create: `src/promptharness/core/db.py`, `tests/test_db.py`

**Interfaces:**
- Consumes: all models.
- Produces `class Database(path: Path)` (opens connection, runs `migrate()`, `row_factory=sqlite3.Row`, `PRAGMA foreign_keys=ON`, `close()`):
  - Providers: `save_provider(p: Provider) -> None` (upsert by name), `get_provider(name) -> Provider | None`, `list_providers() -> list[Provider]`, `delete_provider(name) -> None`.
  - Harnesses: `save_harness(h: Harness) -> None` (upsert by name; stores the prompt as a `prompt_versions` row keyed by hash, replaces the harness's cases), `get_harness(name) -> Harness | None`, `list_harnesses() -> list[Harness]`, `delete_harness(name) -> None`, `set_accepted(name: str, model: ModelRef, run_id: int) -> None`.
  - Runs: `save_run(run: Run) -> int` (inserts run + results, returns id and sets `run.id`/result ids), `get_run(run_id) -> Run | None`, `list_runs(harness: str | None = None) -> list[Run]` (newest first, results included), `set_manual_verdict(result_id: int, verdict: bool | None) -> CaseResult` (recomputes status via `runner`-independent helper below).
  - `schema_version() -> int`; module constant `MIGRATIONS: list[str]` (list of SQL scripts; index i migrates version i → i+1). Initial migration creates `meta, providers, prompt_versions, harnesses, cases, runs, case_results`.
  - `final_status(checks: list[CheckResult], error: str | None, judge_error: bool, manual_verdict: bool | None) -> Status` lives in `core/models.py`-adjacent module `core/status.py` (create it) with rules: `error` if error; `judge_error` if judge_error and no failed deterministic check; `fail` if any check failed or manual_verdict is False; `pass` if manual_verdict is True, or checks non-empty and all passed; else `manual`.

- [ ] **Step 1: Write failing tests** in `test_db.py`:
  - `test_fresh_db_has_latest_schema_version`: `schema_version() == len(MIGRATIONS)`.
  - `test_reopen_does_not_remigrate` (open twice on same path, data preserved).
  - `test_migration_from_older_version`: create DB with only first migration applied (patch `MIGRATIONS` with an extra no-op `ALTER TABLE harnesses ADD COLUMN x TEXT`), reopen, version advanced.
  - `test_provider_roundtrip_and_upsert`; `test_provider_stores_env_name_only` (no column other than `api_key_env` holds key material).
  - `test_harness_roundtrip` including expectations, documents, accepted model; `test_save_harness_replaces_cases`.
  - `test_run_roundtrip_with_results_and_checks`.
  - `test_set_manual_verdict_updates_status`: result with `status="manual"`, verdict True → `"pass"`; False → `"fail"`.
  - `test_final_status_table`: parametrised over the rules above.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** JSON-encode nested structures (expectation, checks, request, response, headers) in TEXT columns; timestamps ISO-8601 UTC strings.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: sqlite storage with migrations`.

---

### Task 5: LLM client

**Files:**
- Create: `src/promptharness/core/client.py`, `tests/test_client.py`

**Interfaces:**
- Consumes: `Provider`, `PromptVersion`.
- Produces:
  - `ChatResult(text: str, prompt_tokens: int|None, completion_tokens: int|None, latency_ms: int, request: dict, response: dict, warnings: list[str])` (dataclass).
  - `class ClientError(Exception)` with `kind: Literal["auth","timeout","rate_limit","config","other"]`.
  - `class ChatClient(Protocol)`: `async def chat(self, provider: Provider, model: str, messages: list[dict], params: PromptVersion | dict) -> ChatResult`; `async def list_models(self, provider: Provider) -> list[str]`.
  - `class OpenAIChatClient` implementing it. Builds `AsyncOpenAI(base_url=provider.base_url, api_key=os.environ[provider.api_key_env], default_headers=provider.headers, timeout=provider.timeout or 60, max_retries=provider.max_retries if not None else 2)` per call. Missing env var → `ClientError("config", "environment variable X is not set")`. Maps `openai.AuthenticationError/PermissionDenied`→auth, `APITimeoutError`→timeout, `RateLimitError`→rate_limit, others→other. Token-limit param name from `provider.max_tokens_param`. Omits params that are `None`. On `BadRequestError` whose message names `temperature` or the token param (or any key in `extra_params`), drops that param, retries once, and appends a warning `"dropped param: <name>"`. Empty/`None` message content → `text=""`.

- [ ] **Step 1: Write failing tests** in `test_client.py`, monkeypatching `promptharness.core.client.AsyncOpenAI` with a fake whose `chat.completions.create` is an async stub:
  - `test_chat_returns_text_usage_and_latency`.
  - `test_missing_env_var_is_config_error`: message contains the var name.
  - `test_uses_max_completion_tokens_when_configured`: assert kwarg captured.
  - `test_none_params_omitted`.
  - `test_empty_content_becomes_empty_string`.
  - `test_error_mapping` parametrised: construct `openai.AuthenticationError`, `RateLimitError`, `APITimeoutError` instances (via `httpx.Response`/`Request` stubs) → kinds auth/rate_limit/timeout.
  - `test_unsupported_param_dropped_with_warning`: first call raises `BadRequestError` mentioning `temperature`; second succeeds; `result.warnings == ["dropped param: temperature"]` and the second call lacked `temperature`.
  - `test_list_models_returns_ids_sorted`; `test_list_models_failure_is_client_error`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per the signatures. `request` records `{base_url, model, messages, params}` (never the key).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: openai-compatible chat client`.

---

### Task 6: Judge

**Files:**
- Create: `src/promptharness/core/judge.py`, `tests/test_judge.py`, `tests/conftest.py` additions (`FakeClient`)

**Interfaces:**
- Consumes: `ChatClient`, `Provider`, `ModelRef`, `CheckResult`.
- Produces: `class JudgeError(Exception)`; `async def run_judge(client: ChatClient, provider: Provider, model: str, judge_prompt: str, case_input: str, output: str) -> CheckResult` — sends a system message demanding JSON `{"pass": bool, "reason": str}` with temperature 0 and a user message containing the judge prompt, the input, and the output; strict-parses (`json.loads` after stripping one code fence; `pass` must be `bool`, `reason` str); on malformed output retries the call once; second failure raises `JudgeError`. Returns `CheckResult(name="judge", passed=..., reason=...)`.
- `conftest.py` produces `FakeClient(script)` implementing `ChatClient`, where `script` is a list of `str | Exception | callable`, consumed in order per `chat` call; records `calls`; `list_models` returns configured ids.

- [ ] **Step 1: Write failing tests** in `test_judge.py`:
  - `test_judge_pass` (`'{"pass": true, "reason": "ok"}'` → passed, reason "ok").
  - `test_judge_fail`.
  - `test_malformed_then_valid_retries_once` (two calls made).
  - `test_malformed_twice_raises_judge_error`.
  - `test_pass_must_be_bool` (`'{"pass": "yes", "reason": "x"}'` treated as malformed).
  - `test_judge_uses_temperature_zero` (inspect `FakeClient.calls`).
  - `test_client_error_propagates` (a `ClientError` is not swallowed).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: llm judge with strict parsing`.

---

### Task 7: Runner

**Files:**
- Create: `src/promptharness/core/runner.py`, `tests/test_runner.py`

**Interfaces:**
- Consumes: everything above; `Database` only through arguments (runner does not import it).
- Produces:
  - `@dataclass RunSettings(concurrency: int = 2)`.
  - `async def evaluate_case(case: Case, prompt: PromptVersion, provider: Provider, model: str, client: ChatClient, judge: tuple[Provider, str] | None) -> CaseResult` — loads documents, renders, calls the client, runs checks, then the judge (skipped if any deterministic check failed or `judge is None` or no `judge_prompt`), and sets status via `final_status`. Never raises: `DocumentError`, `TemplateRenderError`, `ClientError` become `status="error"` with `error` text (client errors prefixed `"<kind>: "`); `JudgeError` becomes `judge_error`.
  - `async def run_harness(harness: Harness, target: ModelRef, providers: dict[str, Provider], client: ChatClient, judge_model: ModelRef | None = None, settings: RunSettings = RunSettings(), on_result: Callable[[CaseResult], None] | None = None, only_case: str | None = None) -> Run` — runs cases under an `asyncio.Semaphore(settings.concurrency)` via `asyncio.gather`, calls `on_result` as each finishes, returns a `Run` with results in case order. Unknown/disabled provider → every case `error`.
  - `async def run_matrix(harness, targets: list[ModelRef], providers, client, judge_model, settings, on_result: Callable[[ModelRef, CaseResult], None] | None) -> list[Run]` — runs targets sequentially-by-model sharing one semaphore-limited pool per model.

- [ ] **Step 1: Write failing tests** in `test_runner.py` using `FakeClient`:
  - `test_pass_when_checks_pass`; `test_fail_when_must_include_missing`; `test_manual_when_no_expectations`.
  - `test_template_error_is_case_error_and_others_continue` (two cases, first has bad template).
  - `test_document_error_is_case_error` (binary file).
  - `test_client_errors_become_case_errors` parametrised over `ClientError` kinds; `error` starts with `"auth: "` etc.
  - `test_missing_provider_errors_all_cases`.
  - `test_judge_skipped_after_deterministic_failure` (FakeClient call count shows no judge call).
  - `test_judge_error_status_on_malformed_judge`.
  - `test_concurrency_limit_respected`: FakeClient callable sleeps and tracks max concurrent in-flight; `concurrency=2` over 6 cases → max in-flight == 2.
  - `test_on_result_called_per_case_as_completed`.
  - `test_only_case_runs_single_case`.
  - `test_results_record_request_tokens_latency`.
  - `test_run_matrix_returns_run_per_target`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Messages are `[system (omitted if empty), user(rendered)]`. Wrap the whole per-case body in a catch-all so unexpected exceptions also become `error` (with the exception type and message).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: async run engine`.

---

### Task 8: Export / import

**Files:**
- Create: `src/promptharness/core/portable.py`, `tests/test_portable.py`

**Interfaces:**
- Consumes: `Database`, `Harness`, `Run`.
- Produces: `FORMAT_VERSION = 1`; `class PortableError(Exception)`; `export_harness(db: Database, name: str, fmt: Literal["yaml","json"] = "yaml", inline_documents: bool = False) -> str` (includes the accepted run's outputs under `accepted_outputs: {case_name: output}` when an accepted run exists; with `inline_documents` each case gets `document_texts: [{name, text}]`); `parse_harness(text: str, fmt: str | None = None) -> tuple[Harness, dict[str, str]]` (returns harness and accepted outputs; format sniffed if `fmt` None; raises `PortableError` for malformed content, missing/greater `format_version`, or duplicate case names); `import_harness(db: Database, text: str, overwrite: bool = False) -> Harness` (raises `PortableError("harness 'x' exists")` if present and not `overwrite`; if accepted outputs are present and the harness has an accepted model, stores a synthetic accepted run with those outputs and sets it as accepted).

- [ ] **Step 1: Write failing tests** in `test_portable.py`:
  - `test_yaml_roundtrip_equal` (export then parse → equal `Harness`, equal accepted outputs).
  - `test_json_roundtrip_equal`.
  - `test_export_contains_no_secrets` (provider env names absent; only `provider:model` string used).
  - `test_inline_documents_included`.
  - `test_unknown_future_format_version_rejected` and `test_missing_format_version_rejected` (`PortableError`, DB untouched).
  - `test_duplicate_case_names_rejected_before_db_write`.
  - `test_import_existing_requires_overwrite`; `test_import_overwrite_replaces`.
  - `test_import_restores_accepted_outputs_as_accepted_run`.
  - `test_malformed_yaml_raises_portable_error`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** with `yaml.safe_load`/`safe_dump(sort_keys=False)`; the synthetic accepted run uses empty request/response and `status="manual"`-derived from stored outputs.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: harness yaml/json export and import`.

---

### Task 9: CLI

**Files:**
- Modify: `src/promptharness/cli.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: `Database`, `run_matrix`, `portable`, `OpenAIChatClient`.
- Produces Typer commands: `promptharness` (no subcommand → `tui.app.run_app()`, imported lazily), `run HARNESS --model provider:model [--model ...] [--judge provider:model] [--concurrency N] [--case NAME]`, `export HARNESS [--format yaml|json] [--inline-documents] [--out PATH]`, `import PATH [--overwrite]`, `provider add NAME --base-url URL --api-key-env VAR`, `provider list`. A module-level `client_factory: Callable[[], ChatClient] = OpenAIChatClient` that tests monkeypatch. `run` saves each `Run` to the DB, prints a case × model table (plain text), exits 1 if any result is `fail`/`error`/`judge_error`, else 0.

- [ ] **Step 1: Write failing tests** in `test_cli.py` with `typer.testing.CliRunner`, `FakeClient` via `client_factory`, and a harness saved into the temp DB:
  - `test_run_all_pass_exit_zero`; `test_run_with_failure_exit_one`; `test_run_manual_only_exit_zero`.
  - `test_run_unknown_harness_exit_two_with_message`; `test_run_bad_model_spec_exit_two`.
  - `test_run_persists_runs` (`db.list_runs()` length equals models).
  - `test_export_then_import_roundtrip` via `--out` file; `test_import_existing_without_overwrite_exit_one`.
  - `test_provider_add_and_list`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -v` → all PASS.
- [ ] **Step 5: Commit** `feat: cli run/export/import/provider`.

---

### Task 10: TUI shell and Providers screen

**Files:**
- Create: `src/promptharness/tui/{__init__,app,providers}.py`, `tui/app.tcss`, `tests/test_tui_smoke.py`

**Interfaces:**
- Consumes: `Database`, `ChatClient`.
- Produces: `PromptHarnessApp(App)` with constructor `PromptHarnessApp(db: Database | None = None, client: ChatClient | None = None)`, attributes `self.db`, `self.client`; `TabbedContent` with panes `harnesses`, `studio`, `runs`, `providers` (keys `1`–`4`), dark theme CSS, `q` quits; `run_app() -> None`. `ProvidersPane(Widget)`: `DataTable` of providers; bindings `a` add, `e` edit, `d` toggle enabled, `t` test connection, `m` manual model entry; add/edit uses a `ModalScreen[Provider | None]` form. Test connection calls `client.list_models`; success shows the count in a notification, failure (including `ClientError`) shows the error and offers manual model entry (models stored per provider in `meta`-adjacent table `provider_models(provider, model)` added as migration 2 with `Database.save_models(provider, models)` / `list_models(provider) -> list[str]`).

- [ ] **Step 1: Write failing tests** in `test_tui_smoke.py` using `app.run_test()`:
  - `test_app_starts_and_tabs_switch` (press `2`, active tab is `studio`).
  - `test_add_provider_via_form`: press `4`, `a`, fill name/base_url/api_key_env fields, submit; `db.list_providers()` has it.
  - `test_connection_failure_does_not_crash`: FakeClient raises `ClientError("auth", ...)`; press `t`; app still running and no uncaught exception.
  - `test_models_roundtrip_in_db` in `test_db.py`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Use `Input`, `Button` ids `name`, `base_url`, `api_key_env`, `submit`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: tui shell and providers screen`.

---

### Task 11: Studio

**Files:**
- Create: `src/promptharness/tui/studio.py`; modify `tui/app.py` to mount it
- Test: `tests/test_tui_smoke.py`

**Interfaces:**
- Consumes: `run_harness`/`evaluate_case`, `PromptVersion`, `Case`, `Database`.
- Produces `StudioPane(Widget)`: model selector (`Select` of enabled `provider:model`, populated from stored models), `TextArea` ids `system` and `template`, a `ListView` of cases with `n` new / `x` delete / `Enter` edit (modal: name, input, document paths comma-separated, expectation fields: must-include, must-not-include, regex toggle, normalized/exact, JSON, judge prompt), `r` run selected case, `R` run all, scrollable wrapped `RichLog`/`Static` output pane with per-case status and check details, `ctrl+z`/`ctrl+y` step through in-session prompt edit history (snapshot of system+template+params on each run and on 2-second edit pause; `PromptHistory` class in the same file with `push(PromptVersion)`, `undo() -> PromptVersion | None`, `redo()`), `s` save as harness (modal for name/description; saves prompt, cases, model, and stores the latest studio results as an accepted run via `db.save_run` + `db.set_accepted`), `v` set manual verdict (y/n) on the selected result. Runs execute in a Textual worker (`run_worker(..., exclusive=False)`), updating the pane per result so the UI never blocks.

- [ ] **Step 1: Write failing tests**:
  - `test_prompt_history_undo_redo` (pure unit: push 3 versions, undo twice, redo once; pushing identical hash twice stores once; undo at start returns `None`).
  - `test_studio_run_one_case_shows_output`: FakeClient returns `"hello"`; add a case programmatically, press `r`; output pane text contains `hello`.
  - `test_studio_failure_shown_not_crash`: FakeClient raises `ClientError("timeout", ...)`; output shows `timeout`.
  - `test_save_as_harness_persists_harness_and_accepted_run`: after run and `s` + name, `db.get_harness(name).accepted_run_id` is set and `db.get_run(...)` has the output.
  - `test_manual_verdict_key_updates_status`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Studio holds cases in memory until saved; saving an existing name asks to overwrite.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: prompt studio screen`.

---

### Task 12: Harnesses, regression matrix, Runs

**Files:**
- Create: `src/promptharness/tui/{harnesses,matrix,runs}.py`; modify `tui/app.py`
- Test: `tests/test_tui_smoke.py`

**Interfaces:**
- Consumes: `run_matrix`, `portable`, `Database`.
- Produces:
  - `HarnessesPane`: `DataTable` of harnesses (name, accepted model, case count); keys `Enter` open in Studio (loads prompt/cases/model), `m` regression run (modal multi-select of enabled `provider:model` plus optional judge), `d` duplicate, `D` delete (confirm), `e` export (modal for path + format), `i` import (modal for path; `PortableError`/existing-name shown inline, with an overwrite confirm).
  - `MatrixScreen(Screen)`: constructed with `harness: Harness` and `targets: list[ModelRef]`; case × model `DataTable` filled live from worker callbacks (cells show `pass`/`fail`/`manual`/`error`/`judge_error`/`…` with colour); `Enter` on a cell pushes a detail view (output, checks with reasons, warnings, error, tokens, latency) where `v` sets the manual verdict; `c` pushes Compare (Task 13); `Esc` back. Runs are saved to the DB as each model finishes.
  - `RunsPane`: `DataTable` of runs (time, harness, model, pass/fail counts) from `db.list_runs()`; `Enter` opens that run in `MatrixScreen` read-only mode (`MatrixScreen.from_runs(runs: list[Run])`); `r` = re-test on replacement: modal to choose a different `provider:model`, then starts a new run with the same harness, prompt hash, and judge settings (Task 13 adds the shared helper).

- [ ] **Step 1: Write failing tests**:
  - `test_regression_matrix_fills_cells_for_two_models` (FakeClient; two cases × two models; after run, four cells have expected statuses).
  - `test_matrix_error_cell_shown_and_app_survives`.
  - `test_matrix_detail_view_shows_check_reasons`.
  - `test_runs_pane_lists_runs_after_regression`.
  - `test_import_via_tui_reports_portable_error` (bad file → message visible, DB unchanged).
  - `test_duplicate_harness_creates_copy_named_with_suffix`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: harnesses, regression matrix and runs screens`.

---

### Task 13: Compare and re-test on replacement

**Files:**
- Create: `src/promptharness/tui/compare.py`; modify `tui/matrix.py`, `tui/runs.py`
- Create: `src/promptharness/core/retest.py`
- Test: `tests/test_runner.py` (retest), `tests/test_tui_smoke.py`

**Interfaces:**
- Consumes: `Run`, `Harness`, `Database`, `run_harness`.
- Produces:
  - `core/retest.py`: `def retest_config(run: Run, new_model: ModelRef) -> dict` returning `{"harness": run.harness, "target": new_model, "judge_model": run.judge_model}`, raising `ValueError` if `new_model == run.model`; `async def retest(db, run, new_model, providers, client, settings, on_result=None) -> Run` (loads the harness by name, warns via `Run`-level return if the harness's current prompt hash differs from `run.prompt_hash` by raising `RetestWarning` only when `strict=True`; default continues using the current harness prompt).
  - `CompareScreen(Screen)`: constructed with `case_name: str`, `columns: list[tuple[str, CaseResult | None]]` (label, result), the accepted output first when available; renders equal-width side-by-side scrollable wrapped panes each headed by label, status, latency, tokens; `←/→` move focus, `Esc` back.
  - Matrix `c` and Harness detail open Compare for the highlighted case with the accepted run (from `harness.accepted_run_id`) as first column.

- [ ] **Step 1: Write failing tests**:
  - `test_retest_config_swaps_model_keeps_judge`; `test_retest_same_model_raises`.
  - `test_retest_runs_with_new_model_and_saves` (FakeClient; `db.list_runs()` grows by one with the new model).
  - `test_compare_screen_shows_accepted_first_then_models` (labels order `["accepted: openai:gpt-4o", "groq:llama"]`, texts present).
  - `test_compare_handles_missing_result_column` (`None` result shows "no result").
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -v` → all PASS.
- [ ] **Step 5: Commit** `feat: compare view and re-test on replacement`.

---

### Task 14: Example harness, README, final verification

**Files:**
- Create: `examples/summarize.harness.yaml`, `README.md`; extend `tests/test_portable.py`

**Interfaces:**
- Consumes: `portable.parse_harness`.

- [ ] **Step 1: Write failing test** `test_example_harness_parses`: `parse_harness(Path("examples/summarize.harness.yaml").read_text())` returns a harness with exactly two cases, valid expectations, and an `accepted_model`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Write the example** (two tiny cases, e.g. a one-sentence summary with a must-include and a JSON-output case with a schema) and **README**: install with uv, required env vars per provider (e.g. `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `GROQ_API_KEY`), provider examples (OpenAI, OpenRouter, Groq, Together, local vLLM `http://localhost:8000/v1`, Ollama `http://localhost:11434/v1`), key bindings table, the studio → save harness → regression run → compare → re-test flow, CLI usage including exit codes, export/import, data location and `PROMPTHARNESS_HOME`.
- [ ] **Step 4: Verify.** Run `uv run pytest -v` → all PASS; `uv run promptharness --help` lists run/export/import/provider; `uv run promptharness import examples/summarize.harness.yaml` succeeds against a temp `PROMPTHARNESS_HOME`; launch the TUI in a pty once to confirm it starts and `q` exits.
- [ ] **Step 5: Commit** `docs: readme and example harness`.
