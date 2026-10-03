# PromptHarness

A local terminal app (Python, [Textual](https://textual.textualize.io/)) for iterating on an LLM prompt and then regression-testing it across models and providers.

You work out a prompt in a **studio** against a handful of test cases, save it as a **harness** with an accepted model and its accepted outputs, and later re-run that harness against other models (for example when a model is being replaced) to see which cases still pass and why the others do not. Everything works with any OpenAI-compatible chat completions endpoint (OpenAI, OpenRouter, Groq, Together, vLLM, Ollama, ...).

Nothing leaves your machine except requests to the provider base URLs you configure. There is no telemetry, cloud sync, account or web UI.

## Features

- **Studio:** edit a system prompt and a Jinja2 user template, attach test cases (inline text and/or documents), run one case or all, and step back through your prompt edits.
- **Checks:** must-include / must-not-include (substring or regex), exact or normalized match, JSON and JSON Schema validity, an optional LLM judge on an independent model, and manual pass/fail.
- **Harnesses:** save a prompt, its cases, expectations, accepted model and accepted outputs, then re-run it against any mix of models and providers and read a case × model matrix.
- **Compare and re-test:** view outputs side by side against the accepted output, or copy a past run and swap the model to see if a replacement still passes.
- **Headless:** `promptharness run` exits non-zero on failures, so it works in CI.
- **Portable:** harnesses export to YAML or JSON so they can live in git. API keys are never stored, only the names of the environment variables that hold them.

## Install

Requires Python 3.11+.

```sh
git clone https://github.com/pmirvine/prompt-harness.git
cd prompt-harness
uv venv && uv pip install -e .
```

or, without uv:

```sh
pip install -e .
```

Launch the TUI:

```sh
promptharness            # or: uv run promptharness
```

`promptharness --help` lists the subcommands (`run`, `export`, `import`, `provider`).

## Quickstart

### The starter test case

The repo ships a starter harness, [`examples/quickstart.harness.yaml`](examples/quickstart.harness.yaml). It is a model-agnostic smoke test of three tiny deterministic cases that any chat model should pass. It has no accepted model, so you choose what to try.

| Case | Prompt (abridged) | What it checks |
|------|-------------------|----------------|
| `capital-of-france` | "What is the capital of France? Answer with just the city name." | Output must include `Paris` and must not include `London` |
| `extract-person-json` | Extract `name` and `age` from "Ada Lovelace was 36 years old when she died." | Output must be valid JSON matching a schema (`name` string, `age` integer) |
| `three-colors` | "List exactly three different colors, lowercase, separated by commas." | Output must match a regex for `a, b, c` |

The shared prompt uses `temperature: 0.0` and `max_tokens: 2048`. The generous limit is deliberate, see the note on reasoning models below.

### Try it yourself

With a local server such as LM Studio, Ollama or vLLM running (LM Studio's server is on port 1234 by default):

```sh
# 1. Register the server (no API key needed locally)
promptharness provider add lmstudio --base-url http://localhost:1234/v1

# 2. Load the starter harness
promptharness import examples/quickstart.harness.yaml

# 3. Run it against a model you have loaded (provider:model)
promptharness run quickstart --model lmstudio:your-model-id
```

Replace `your-model-id` with an id from `curl http://localhost:1234/v1/models`, and change the base URL if the server runs on another machine. You should see:

```
case                 lmstudio:your-model-id
capital-of-france    pass
extract-person-json  pass
three-colors         pass
3 pass, 0 fail, 0 error, 0 judge_error, 0 manual
```

and exit code 0. A failure prints the reason underneath the table. To also exercise the LLM judge, add a `judge_prompt` to a case in the harness and pass `--judge lmstudio:your-model-id`.

In the TUI the same steps are: `4` then `a` to add the provider, `t` to fetch its models, `1` then `i` to import `examples/quickstart.harness.yaml`, and `m` to run it against one or more models.

**Reasoning models** (those that "think" before answering) spend part of `max_tokens` on hidden reasoning. If the limit is too low they return an empty answer. The quickstart sets `max_tokens: 2048` for this reason, and a result that hit the limit carries a warning such as `empty answer: the model used its token limit ... raise max_tokens`.

## API keys and environment variables

PromptHarness stores only the **name** of the environment variable that holds a provider's key, never the key itself. The key is read from the environment at the moment each request is made, so either export it in the shell you launch from or put it in a `.env` file (see [Using a `.env` file](#using-a-env-file)):

| Provider   | Typical env var name  |
|------------|-----------------------|
| OpenAI     | `OPENAI_API_KEY`      |
| OpenRouter | `OPENROUTER_API_KEY`  |
| Groq       | `GROQ_API_KEY`        |
| Together   | `TOGETHER_API_KEY`    |
| Local (vLLM, Ollama) | none required by the server |

The env var name is whatever you enter when adding the provider; the names above are conventions. If a provider has an env var name and that variable is unset, its requests fail with a per-case `error` ("environment variable ... is not set"). A local server such as vLLM or Ollama needs no env var: leave the name empty (omit `--api-key-env` on the CLI) and a placeholder key is sent.

### Using a `.env` file

[`.env.sample`](.env.sample) lists the base URL and key variable for OpenAI, Anthropic, Google Gemini, Amazon Bedrock, OpenRouter, Groq, Together, Mistral, xAI, DeepSeek, and local servers (LM Studio, Ollama, vLLM, llama.cpp).

PromptHarness loads a `.env` file automatically on startup, for the TUI and for every subcommand:

```sh
cp .env.sample .env          # .env is git-ignored
$EDITOR .env                 # replace the placeholder keys you need
promptharness                # run from the directory containing .env
```

Variables are looked up in this order, highest priority first:

1. variables already set in your shell (a `.env` never overrides them);
2. `./.env` in the directory you run `promptharness` from (parent directories are not searched);
3. `.env` in the PromptHarness data directory (see [Where data lives](#where-data-lives)), for keys you want available from any directory.

Empty values (`KEY=`) are treated as unset, values are never printed, and a missing `.env` is not an error. If a `.env` exists but cannot be read, a one-line warning naming the file goes to stderr.

Provider registration is unchanged: `promptharness provider add` still takes `--api-key-env` (the variable *name*). The `*_BASE_URL` lines in the sample are only a convenience. They are not read by PromptHarness, so to use them in `provider add`, load the file into your shell first:

```sh
set -a; source .env; set +a  # export the variables into this shell
promptharness provider add openai --base-url "$OPENAI_BASE_URL" --api-key-env OPENAI_API_KEY
```

Anthropic, Gemini and Bedrock are reached through their OpenAI-compatible endpoints, which can ignore or reject some OpenAI parameters; check each provider's documentation.

## Provider setup

Providers are managed on the Providers tab (`4`) or with `promptharness provider add`.

| Provider   | Name         | Base URL                          | Env var              |
|------------|--------------|-----------------------------------|----------------------|
| OpenAI     | `openai`     | `https://api.openai.com/v1`       | `OPENAI_API_KEY`     |
| OpenRouter | `openrouter` | `https://openrouter.ai/api/v1`    | `OPENROUTER_API_KEY` |
| Groq       | `groq`       | `https://api.groq.com/openai/v1`  | `GROQ_API_KEY`       |
| Together   | `together`   | `https://api.together.xyz/v1`     | `TOGETHER_API_KEY`   |
| vLLM (local)   | `vllm`   | `http://localhost:8000/v1`        | none (leave empty)   |
| Ollama (local) | `ollama` | `http://localhost:11434/v1`       | none (leave empty)   |
| LM Studio (local) | `lmstudio` | `http://localhost:1234/v1`   | none (leave empty)   |

In the TUI: press `4`, then `a`, fill in Name, Base URL and the API key env var **name** (empty for a local server), and save. Then press `t` to test the connection; on success the provider's model list is fetched and stored. If listing fails (some servers do not expose `/models`), the models editor opens so you can type model ids one per line (`m` reopens it later).

From the CLI:

```sh
promptharness provider add openai     --base-url https://api.openai.com/v1      --api-key-env OPENAI_API_KEY
promptharness provider add openrouter --base-url https://openrouter.ai/api/v1   --api-key-env OPENROUTER_API_KEY
promptharness provider add groq       --base-url https://api.groq.com/openai/v1 --api-key-env GROQ_API_KEY
promptharness provider add together   --base-url https://api.together.xyz/v1    --api-key-env TOGETHER_API_KEY
promptharness provider add vllm       --base-url http://localhost:8000/v1
promptharness provider add ollama     --base-url http://localhost:11434/v1
promptharness provider list
```

`provider add` on an existing name updates only the options you pass and keeps the rest (including whether it is enabled); it prints `Added` or `Updated`. `--max-tokens-param max_completion_tokens` (also a choice in the TUI form) sends the token limit as `max_completion_tokens`, which some newer OpenAI models require. If a provider rejects a parameter (for example `temperature`), the request is retried without it and the result carries a `dropped param: ...` warning. Models are referred to everywhere as `provider:model`, for example `openai:gpt-4o-mini` or `ollama:llama3.2`.

## Key bindings

Global (on the main screen): `1` Harnesses, `2` Studio, `3` Runs, `4` Providers, `q` quit. The tab keys are disabled while a sub-screen or dialog is open. The footer always shows the keys available at the current focus. Dialogs close with `esc`.

| Screen | Key | Action |
|--------|-----|--------|
| Harnesses | `enter` | Open the harness in the Studio (with its accepted model) |
| | `m` | Regression run: pick models and an optional judge |
| | `d` / `D` | Duplicate / delete (delete asks for confirmation; run history is kept) |
| | `e` / `i` | Export / import (dialogs ask for a file path) |
| Studio (focus on the case list; `tab` moves between fields) | `n` | New case |
| | `enter` | Edit the selected case |
| | `x` | Delete the selected case |
| | `r` / `R` | Run the selected case / run all cases |
| | `v` | Manual verdict for the selected case (`y` pass, `n` fail, `c` clear) |
| | `s` | Save as harness |
| Studio (anywhere) | `ctrl+r` | Run the selected case |
| | `ctrl+s` | Save as harness |
| | `alt+left` / `alt+right` | Step back / forward through prompt history (`ctrl+z` / `ctrl+y` when no text editor has focus) |
| Runs | `enter` | Open the highlighted run, or the marked runs, side by side |
| | `space` | Mark / unmark a run for side-by-side viewing |
| | `r` | Re-test the highlighted run's harness on another model |
| Providers | `a` / `e` | Add / edit |
| | `d` | Enable / disable |
| | `t` | Test connection and fetch models |
| | `m` | Edit the model list by hand |
| Regression matrix | `enter` | Open the details for the highlighted cell |
| | `c` | Compare the highlighted case across models |
| | `esc` | Back (leaving a running matrix cancels it; finished models are kept) |
| Result details | `v` | Manual verdict |
| | `c` | Compare this case across models |
| | `esc` | Back |
| Compare | `left` / `right` | Move between panes |
| | `esc` | Back |

## Walk-through: studio to re-test

1. **Studio (`2`).** Choose a model under test (from the dropdown, or type `provider:model` in the manual box and press Enter). Optionally choose a judge model. Write a system prompt and a Jinja2 user template (`{{ input }}` and `documents` are available; the default is `{{ input }}`), and set temperature and max tokens if you want. Add cases with `n`: a name, input text, optional document paths (plain UTF-8 text files, comma-separated), and expectations (see below). Run cases with `r`/`R`/`ctrl+r` and read the output and check results. Edit and re-run until it is right; `alt+left`/`alt+right` walk through earlier prompt versions.
2. **Save harness (`s` or `ctrl+s`).** Give it a name. The prompt, cases and the model under test are stored as a harness. If you have fresh results for the current prompt, model and judge, they are stored as the harness's **accepted run**, which later runs are compared against. If not, the harness is saved without an accepted run and a warning says so.
3. **Regression run (Harnesses, `m`).** Select one or more models (space toggles; or type `provider:model`, comma-separated) and an optional judge. A matrix of case x model fills in live. `enter` on a cell shows the request outcome, checks with reasons, warnings, tokens and latency; `v` sets a manual verdict. Each finished model is saved as a run.
4. **Compare (`c` in the matrix or in cell details).** Shows one case across the accepted run (when the harness has one) and every model in the matrix, pane by pane (`left`/`right`).
5. **Re-test on replacement (Runs, `3`, then `r`).** Pick a past run and a replacement model (pick from the list or type `provider:model`; the run's own model is rejected). The harness is re-run on the new model with the same judge as the original run, using the harness's **current** prompt; you are told if the prompt changed since that run. The result is saved as a new run. Mark two or more runs with `space` and press `enter` to view them side by side.

## Checks and statuses

Each case can have any combination of these expectations, evaluated in this order:

1. must include: each pattern must appear in the output
2. must not include: each pattern must be absent
3. exact: output equals the given text
4. normalized: equals the given text after trimming, collapsing whitespace and case-folding
5. JSON output: output parses as JSON (a surrounding Markdown code fence is tolerated)
6. JSON Schema: parsed output validates against the schema (Draft 2020-12)
7. judge: if a judge model is set and the case has a judge prompt, the judge model is asked for a `{"pass": bool, "reason": str}` verdict (it gets one retry on malformed output). The judge runs **only when all deterministic checks above passed**.

Include/exclude patterns are literal substrings unless the case's regex option is ticked, in which case they are regular expressions.

A case ends with one of these statuses:

| Status | Meaning |
|--------|---------|
| `pass` | At least one check ran and all passed, or a manual verdict of pass |
| `fail` | A check failed, or a manual verdict of fail |
| `error` | The request itself failed (auth, timeout, rate limit, missing env var, unknown or disabled provider, unreadable document, template error) |
| `judge_error` | Deterministic checks passed but the judge failed or was unavailable (malformed output twice, request error, judge provider unknown or disabled) |
| `manual` | No checks decided the outcome: nothing to check automatically. Review the output and set a verdict with `v` |

Precedence: `error` first; then any failed check or a manual fail gives `fail`; then a manual pass gives `pass` (so a manual pass resolves `manual` and `judge_error` but cannot override a failed check). Provider failures are shown per case and never crash the app.

## CLI

```sh
promptharness run NAME --model openai:gpt-4o-mini [--model groq:llama-3.3-70b-versatile ...] \
    [--judge openai:gpt-4o] [--concurrency 2] [--case CASE_NAME]
promptharness export NAME [--format yaml|json] [--inline-documents] [--out FILE]
promptharness import FILE [--overwrite]
promptharness provider add NAME --base-url URL [--api-key-env VAR] [--max-tokens-param max_tokens|max_completion_tokens]
promptharness provider list
```

`run` executes a saved harness against each `--model` (repeatable), saves every run to the database (so it shows on the Runs tab), and prints a case x model table of statuses, then one line per non-passing cell with the reason (`c1 × openai:gpt-4o-mini: fail — include:ok: pattern not found: 'ok'`), one line per warning, and a summary (`3 pass, 1 fail, 0 error, 0 judge_error, 0 manual`). `--concurrency` (default 2) bounds parallel requests per model, `--case` runs a single case, `--judge` supplies the judge model.

Exit codes for `run`:

| Code | Meaning |
|------|---------|
| `0` | No case ended in `fail`, `error` or `judge_error` (`manual` cases do not fail the run) |
| `1` | At least one case ended in `fail`, `error` or `judge_error` |
| `2` | Usage error: bad `provider:model`, unknown harness, unknown `--case` |

`export` and `import` exit `1` on errors (missing harness, unreadable or invalid file, name already exists without `--overwrite`). Usage errors exit `2`: `export --format` other than `yaml`/`json`, and Typer's own argument errors.

## Export and import

`promptharness export NAME` writes a portable YAML (or JSON with `--format json`) file; the TUI's `e`/`i` on the Harnesses tab do the same. The file contains:

- `format_version` (currently `1`; files with a newer version are rejected)
- `name`, `description`, `prompt` (system, template, temperature, max_tokens, extra_params) and `cases` (input, document paths, notes, expectation)
- `accepted_model` (`provider:model`) and `accepted_outputs` (case name to the accepted run's output), when the harness has an accepted run
- with `--inline-documents` (or the checkbox in the TUI export dialog): each case's `document_texts`, the full text of its documents

Provider settings, env var names, keys, run history and verdicts are **not** exported. On import, inline documents that do not exist at their original path are written under `<data dir>/documents/<harness name>/` and the case is repointed there. If the file has `accepted_outputs`, they become an accepted run for `accepted_model`; those restored outputs carry no check results, so their status is `manual`. Importing a name that exists fails unless you pass `--overwrite` (or confirm in the TUI). An imported harness may reference local document paths; when it runs, those files' contents are sent to the provider you run it against, so review a harness from someone else (its cases' document paths) before running it.

A ready-to-use example with two tiny cases is in [`examples/summarize.harness.yaml`](examples/summarize.harness.yaml):

```sh
promptharness import examples/summarize.harness.yaml
promptharness run summarize-example --model openai:gpt-4o-mini
```

## Where data lives

Everything (providers, harnesses, runs, imported documents) is in one SQLite database, `promptharness.db`, in your platform's user data directory (via `platformdirs`; for example `~/Library/Application Support/promptharness` on macOS and `~/.local/share/promptharness` on Linux). Set `PROMPTHARNESS_HOME` to use a different directory, for a separate set of data or for throwaway experiments:

```sh
PROMPTHARNESS_HOME=/tmp/ph-scratch promptharness
```

## Scope

Network access happens only in requests to the provider base URLs you configure (chat completions and, for connection tests, model listing). Deliberately out of scope: cost tracking, the OpenAI Responses API (only chat completions are used), cloud sync, accounts and a web UI.

## Development

```sh
uv run pytest        # uv installs the dev dependency group (pytest, pytest-asyncio) automatically
```

The tests use a mocked LLM client and make no real API calls.
