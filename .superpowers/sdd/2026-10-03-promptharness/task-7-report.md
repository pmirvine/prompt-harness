# Task 7 report: Runner
- Wrote tests/test_runner.py first; RED: ModuleNotFoundError promptharness.core.runner.
- Implemented src/promptharness/core/runner.py (RunSettings, evaluate_case, run_harness, run_matrix). GREEN: full suite 97 passed.
- Concerns: judge model whose provider is missing/disabled is silently treated as judge=None (judge cases become manual). Missing/disabled target provider error text: "config: provider 'x' is unknown|disabled". Judge token usage is not recorded.

## Fix report (round 1)
- I1: unavailable judge provider -> reason `config: judge provider 'x' is unknown|disabled`; pass/manual cases with judge_prompt become judge_error via final_status, reason appended to warnings. Tests for disabled and unknown.
- I2: on_result wrapped (`except Exception`), failure appended to warnings; test with raising callback. Matrix path goes through run_harness's wrapper.
- I3/M1: missing-provider and template-error tests now assert no client calls and exact/relevant error text.
- M2: unknown only_case raises ValueError before work; test added.
- M3: JudgeError text appended to warnings as "judge: ..."; assertion added.
- Command: `uv run pytest tests/test_runner.py -q` -> 29 passed. Full suite: all passed.
