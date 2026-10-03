# PromptHarness — Design Spec

Date: 2026-10-03

## Purpose

A local Python TUI for iterative LLM prompt development and regression testing across OpenAI-compatible providers. The user attaches test inputs and documents, iterates on a prompt in a studio until output is right, saves that as a harness, then re-runs the harness against other models and providers (including when replacing a model) to see which still satisfy it. Nothing leaves the machine except calls to configured provider base URLs. No cloud sync, accounts, or web UI.

## Success criteria

- `uv pip install -e .` (or `pip install -e .`) installs the package; `promptharness` launches the TUI.
- Studio → save harness → regression run → compare flow works end to end.
- After a model swap, one matrix shows case × model status with per-case reasons.
- Failures (auth, timeout, rate limit, malformed judge output) are shown per case and never crash the app.
- Tests use a mocked LLM client; no real API calls.

## Stack

Python 3.11+, Textual, official `openai` SDK (`AsyncOpenAI`), Jinja2 (sandboxed), pydantic, Typer, PyYAML, jsonschema, SQLite (stdlib). Tooling: uv, hatchling, pytest + pytest-asyncio.

## Architecture

```
promptharness/
  core/            # no Textual imports; fully testable
    models.py      # pydantic: Provider, Case, Expectation, PromptVersion, Harness, Run, CaseResult
    db.py          # SQLite, schema_version + migrations, repository functions
    client.py      # AsyncOpenAI wrapper
    render.py      # Jinja2 rendering, document loading
    checks.py      # deterministic checks
    judge.py       # LLM judge
    runner.py      # async run engine
    portable.py    # YAML/JSON export and import
  cli.py           # Typer entry point
  tui/             # Textual app and screens
examples/          # one example harness, two tiny cases
tests/
```

The core has no Textual dependency. The TUI and CLI are thin layers over it.

## Data model and storage

SQLite is the source of truth. Location: platform data dir, overridable via `PROMPTHARNESS_HOME`.

Tables: `meta(schema_version)`, `providers`, `harnesses`, `prompt_versions`, `cases`, `runs`, `case_results`. Migrations are an ordered list of SQL steps applied up to the stored `schema_version`.

- **Provider**: name, base_url, `api_key_env` (env var name only; the key is read at call time and never stored), optional default headers, `max_tokens_param` (`max_tokens` | `max_completion_tokens`), enabled flag, optional timeout/retry overrides.
- **Model**: provider + model id (a reference, not a table).
- **PromptVersion**: system prompt, Jinja2 user template, temperature, max tokens, `extra_params` JSON. Immutable, identified by a content hash.
- **Case**: name, inline input text, zero or more document paths, notes, and an Expectation.
- **Expectation**:
  - must-include / must-not-include entries (each substring or regex)
  - exact or normalized text match (normalized = collapsed whitespace, case-folded)
  - structured output: must parse as JSON, optional JSON Schema
  - optional judge prompt
  - manual verdict is recorded on results, not in the expectation
- **Harness**: name, description, prompt version, cases, accepted model, accepted run id. The accepted run's outputs are the golden reference shown first in Compare.
- **Run**: harness, prompt hash, model (provider + id), judge model, settings (concurrency, retries, timeout), start/end times.
- **CaseResult**: full request and response, latency, token usage if returned, per-check results with reasons, warnings (e.g. dropped params), status, manual verdict, error text.

### Portability

Export/import to YAML or JSON with a `format_version` field. Contents: harness, prompt, cases, expectations, accepted model, and accepted outputs. Never keys. Document inclusion is optional: paths only by default, or inline text for a self-contained file. Import is upsert-by-name with a confirmation step before overwrite.

### Documents

Stored as paths. Text is read at run time. A non-text or unreadable file produces a clear "unsupported" error on that case rather than a guess. Templates receive `input` (string) and `documents` (list of `{name, text}`).

## Checks and verdicts

Order, cheapest first: call error → must-include/must-not-include → exact/normalized match → structured-output checks → LLM judge → manual verdict. Each check records its own pass/fail and reason. A deterministic failure skips the judge call.

Case status: `pass`, `fail`, `error`, `manual` (no automatic checks decide; awaiting verdict), `judge_error`.

**Judge**: separate configurable model, default temperature 0, expects `{"pass": bool, "reason": str}`. Strict JSON parse; on malformed output retry once; if still malformed the case is `judge_error`, not `fail`. The UI warns when the judge and the model under test are the same.

## Client and runner

- `AsyncOpenAI(base_url, api_key, timeout, max_retries)` built per call from provider config; key read from the named env var at call time. Missing env var is a per-case `error` with a clear message.
- Defaults: concurrency 2, retries 2, timeout 60s; configurable globally and per provider.
- When a provider rejects a sampling param, the client drops it, retries, and records a warning on the result.
- Connection test uses `models.list()`; if unsupported, the user enters model ids manually.
- Chat completions only.
- Runner: `asyncio.Semaphore`, one task per (case, model). Each result is emitted as an event so the UI updates live. All exceptions are caught per case and stored.

## TUI

Keyboard-first, dark, dense; long output wraps and scrolls. Tabs: Harnesses, Studio, Runs, Providers (number keys), with Compare as a pushed screen. Every action has a footer binding.

- **Providers**: add/edit/disable, test connection, manual model entry fallback.
- **Studio**: model picker, system prompt and template editors, case list, wrapped scrollable output pane, in-session prompt edit history (step back/forward), run one / run all, and "Save as harness" (records prompt, cases, expectations, model, and latest outputs as the accepted run).
- **Harnesses**: list, open, duplicate, export/import.
- **Regression run**: choose a harness and one or more provider/model pairs; case × model matrix with pass/fail/manual/error/judge_error filling in live; selecting a cell shows output, check details, and a key to set the manual verdict.
- **Compare**: side-by-side outputs for one case across models, accepted output first.
- **Re-test on replacement**: from any past run, copy its config with the model swapped.
- **Runs**: history list with status summaries.

## CLI

- `promptharness` — launch TUI.
- `promptharness run <harness> --model provider:model [...]` — headless; prints the matrix; exits non-zero if any case is `fail`, `error`, or `judge_error`. `manual` cases are reported but do not fail the run.
- `promptharness export` / `promptharness import` — YAML or JSON.

## Out of scope

Cost tracking (tokens only), Responses API, cloud sync, accounts, web UI.

## Testing

All tests use a fake client. Coverage:
- template rendering (`input`, `documents`, missing documents)
- each check type (substring, regex, normalized/exact, JSON, JSON Schema)
- judge strict parsing and retry
- runner concurrency limit and per-case error capture
- harness YAML/JSON round-trip
- DB migrations
- Textual pilot smoke test

## Deliverables

Installable package with `promptharness` entry point, README (setup with uv, provider examples, required env vars, studio → save harness → regression run flow), one checked-in example harness with two tiny cases, and the test suite.
