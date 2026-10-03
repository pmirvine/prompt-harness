from __future__ import annotations

import json
import re
from typing import Any

from jsonschema import Draft202012Validator

from promptharness.core.models import CheckResult, Expectation, Match

_FENCE = re.compile(r"\A```[^\n]*\n(.*?)\n?```\Z", re.DOTALL)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).casefold()


def _matches(output: str, m: Match) -> tuple[bool, str | None]:
    """Return (found, error). error is set for an invalid regex."""
    if m.regex:
        try:
            return re.search(m.pattern, output) is not None, None
        except re.error as e:
            return False, f"invalid regex {m.pattern!r}: {e}"
    return m.pattern in output, None


def _parse_json(output: str) -> tuple[Any, str | None]:
    text = output.strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, f"output is not valid JSON: {e}"


def run_checks(output: str, exp: Expectation) -> list[CheckResult]:
    results: list[CheckResult] = []

    for m in exp.must_include:
        found, err = _matches(output, m)
        if err:
            results.append(CheckResult(name=f"include:{m.pattern}", passed=False, reason=err))
        else:
            results.append(
                CheckResult(
                    name=f"include:{m.pattern}",
                    passed=found,
                    reason="" if found else f"pattern not found: {m.pattern!r}",
                )
            )

    for m in exp.must_not_include:
        found, err = _matches(output, m)
        if err:
            results.append(CheckResult(name=f"exclude:{m.pattern}", passed=False, reason=err))
        else:
            results.append(
                CheckResult(
                    name=f"exclude:{m.pattern}",
                    passed=not found,
                    reason=f"forbidden pattern present: {m.pattern!r}" if found else "",
                )
            )

    if exp.exact is not None:
        ok = output == exp.exact
        results.append(
            CheckResult(name="exact", passed=ok, reason="" if ok else "output does not exactly match")
        )

    if exp.normalized is not None:
        ok = normalize(output) == normalize(exp.normalized)
        results.append(
            CheckResult(
                name="normalized",
                passed=ok,
                reason="" if ok else "output does not match after normalization",
            )
        )

    parsed: Any = None
    parse_err: str | None = None
    if exp.json_output or exp.json_schema is not None:
        parsed, parse_err = _parse_json(output)

    if exp.json_output:
        results.append(CheckResult(name="json", passed=parse_err is None, reason=parse_err or ""))

    if exp.json_schema is not None:
        if parse_err:
            results.append(CheckResult(name="json_schema", passed=False, reason=parse_err))
        else:
            try:
                err = next(iter(Draft202012Validator(exp.json_schema).iter_errors(parsed)), None)
            except Exception as e:  # invalid schema
                results.append(
                    CheckResult(name="json_schema", passed=False, reason=f"invalid schema: {e}")
                )
            else:
                results.append(
                    CheckResult(
                        name="json_schema",
                        passed=err is None,
                        reason=err.message if err else "",
                    )
                )

    return results
