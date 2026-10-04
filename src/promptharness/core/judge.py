from __future__ import annotations

import json
import re
from collections.abc import Sequence

from promptharness.core.client import ChatClient
from promptharness.core.documents import Document
from promptharness.core.messages import build_user_content
from promptharness.core.models import CheckResult, Provider
from promptharness.core.render import TemplateRenderError, render_template

SYSTEM_PROMPT = (
    "You are a strict evaluator. Judge whether the OUTPUT satisfies the criteria. "
    'Respond with ONLY a JSON object of the form {"pass": true|false, "reason": "<short reason>"} '
    "and nothing else. "
    "Documents may be provided as reference material."
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


def _render_criteria(
    judge_prompt: str, case_input: str, output: str, documents: Sequence[Document], name: str
) -> str:
    try:
        return render_template(
            judge_prompt, {"input": case_input, "output": output, name: list(documents)}
        )
    except TemplateRenderError as e:
        raise JudgeError(f"judge prompt template error: {e}") from e


async def run_judge(
    client: ChatClient,
    provider: Provider,
    model: str,
    judge_prompt: str,
    case_input: str,
    output: str,
    documents: Sequence[Document] = (),
    documents_name: str = "documents",
) -> CheckResult:
    output = output.strip()
    criteria = _render_criteria(judge_prompt, case_input, output, documents, documents_name)
    sections = [f"Criteria:\n{criteria}", f"Input:\n{case_input}"]
    if documents:
        blocks = [f"[{d.index}] {d.name}\n{d.text}" for d in documents]
        sections.append("Documents:\n" + "\n\n".join(blocks))
    sections.append(f"Output:\n{output}")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_content("\n\n".join(sections), documents)},
    ]
    last = ""
    for _ in range(2):
        result = await client.chat(provider, model, messages, {"temperature": 0})
        last = result.text
        parsed = _parse(last)
        if parsed is not None:
            return CheckResult(name="judge", passed=parsed[0], reason=parsed[1])
    raise JudgeError(f"judge returned malformed output twice: {last[:200]!r}")
