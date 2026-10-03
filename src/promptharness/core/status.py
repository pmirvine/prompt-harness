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
    if any(not c.passed for c in checks) or manual_verdict is False:
        return "fail"
    if manual_verdict is True:
        return "pass"
    if judge_error:
        return "judge_error"
    if bool(checks) and all(c.passed for c in checks):
        return "pass"
    return "manual"
