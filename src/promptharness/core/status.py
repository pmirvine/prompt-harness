from __future__ import annotations

from promptharness.core.models import CheckResult, Status


def final_status(
    checks: list[CheckResult],
    error: str | None,
    judge_error: bool,
    manual_verdict: bool | None,
) -> Status:
    if error:
        return "error"
    any_failed = any(not c.passed for c in checks)
    if judge_error and not any_failed:
        return "judge_error"
    if any_failed or manual_verdict is False:
        return "fail"
    if manual_verdict is True or (checks and all(c.passed for c in checks)):
        return "pass"
    return "manual"
