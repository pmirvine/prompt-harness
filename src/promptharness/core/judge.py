from __future__ import annotations

import json
import re

from promptharness.core.client import ChatClient
from promptharness.core.models import CheckResult, Provider

SYSTEM_PROMPT = (
    "You are a strict evaluator. Judge whether the OUTPUT satisfies the criteria. "
    'Respond with ONLY a JSON object of the form {"pass": true|false, "reason": "<short reason>"} '
    "and nothing else."
)

_FENCE = re.compile(r"^```[A-Za-z0-9_-]*\s*\n?(.*?)\n?```$", re.DOTALL)


class JudgeError(Exception):
    pass


def _parse(text: str) -> tuple[bool, str] | None:
    s = text.strip()
    m = _FENCE.match(s)
    if m:
        s = m.group(1).strip()
    try:
        data = json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    passed, reason = data.get("pass"), data.get("reason")
    if not isinstance(passed, bool) or not isinstance(reason, str):
        return None
    return passed, reason


async def run_judge(
    client: ChatClient,
    provider: Provider,
    model: str,
    judge_prompt: str,
    case_input: str,
    output: str,
) -> CheckResult:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Criteria:\n{judge_prompt}\n\nInput:\n{case_input}\n\nOutput:\n{output}"
            ),
        },
    ]
    last = ""
    for _ in range(2):
        result = await client.chat(provider, model, messages, {"temperature": 0})
        last = result.text
        parsed = _parse(last)
        if parsed is not None:
            return CheckResult(name="judge", passed=parsed[0], reason=parsed[1])
    raise JudgeError(f"judge returned malformed output twice: {last[:200]!r}")
