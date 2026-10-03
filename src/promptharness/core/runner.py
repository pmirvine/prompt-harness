from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from promptharness.core.checks import run_checks
from promptharness.core.client import ChatClient, ClientError
from promptharness.core.judge import JudgeError, run_judge
from promptharness.core.models import (
    Case,
    CaseResult,
    CheckResult,
    Harness,
    ModelRef,
    PromptVersion,
    Provider,
    Run,
)
from promptharness.core.render import DocumentError, TemplateRenderError, load_documents, render_user
from promptharness.core.status import final_status


@dataclass
class RunSettings:
    concurrency: int = 2


def _error_result(case: Case, error: str, **extra) -> CaseResult:
    return CaseResult(case_name=case.name, status="error", error=error, **extra)


async def evaluate_case(
    case: Case,
    prompt: PromptVersion,
    provider: Provider,
    model: str,
    client: ChatClient,
    judge: tuple[Provider, str] | None,
) -> CaseResult:
    """Evaluate one case. Never raises; failures become status="error" results."""
    try:
        return await _evaluate(case, prompt, provider, model, client, judge)
    except (DocumentError, TemplateRenderError) as e:
        return _error_result(case, str(e))
    except ClientError as e:
        return _error_result(case, f"{e.kind}: {e}")
    except Exception as e:
        return _error_result(case, f"{type(e).__name__}: {e}")


async def _evaluate(
    case: Case,
    prompt: PromptVersion,
    provider: Provider,
    model: str,
    client: ChatClient,
    judge: tuple[Provider, str] | None,
) -> CaseResult:
    docs = load_documents(case.documents)
    user = render_user(prompt.template, case.input, docs)
    messages: list[dict] = []
    if prompt.system:
        messages.append({"role": "system", "content": prompt.system})
    messages.append({"role": "user", "content": user})

    chat = await client.chat(provider, model, messages, prompt)
    exp = case.expectation
    checks: list[CheckResult] = run_checks(chat.text, exp)

    judge_error = False
    error: str | None = None
    if judge is not None and exp.judge_prompt and all(c.passed for c in checks):
        jprov, jmodel = judge
        try:
            checks.append(
                await run_judge(client, jprov, jmodel, exp.judge_prompt, case.input, chat.text)
            )
        except JudgeError:
            judge_error = True
        except ClientError as e:
            error = f"{e.kind}: {e}"

    return CaseResult(
        case_name=case.name,
        status=final_status(checks, error, judge_error, None),
        output=chat.text,
        request=chat.request,
        response=chat.response,
        latency_ms=chat.latency_ms,
        prompt_tokens=chat.prompt_tokens,
        completion_tokens=chat.completion_tokens,
        checks=checks,
        warnings=list(chat.warnings),
        error=error,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def run_harness(
    harness: Harness,
    target: ModelRef,
    providers: dict[str, Provider],
    client: ChatClient,
    judge_model: ModelRef | None = None,
    settings: RunSettings = RunSettings(),
    on_result: Callable[[CaseResult], None] | None = None,
    only_case: str | None = None,
) -> Run:
    started = _now()
    cases = [c for c in harness.cases if only_case is None or c.name == only_case]
    provider = providers.get(target.provider)
    usable = provider is not None and provider.enabled

    judge: tuple[Provider, str] | None = None
    if judge_model is not None:
        jp = providers.get(judge_model.provider)
        if jp is not None and jp.enabled:
            judge = (jp, judge_model.model)

    sem = asyncio.Semaphore(max(1, settings.concurrency))

    async def one(case: Case) -> CaseResult:
        if not usable:
            reason = "disabled" if provider is not None else "unknown"
            result = _error_result(case, f"config: provider {target.provider!r} is {reason}")
        else:
            async with sem:
                result = await evaluate_case(
                    case, harness.prompt, provider, target.model, client, judge
                )
        if on_result is not None:
            on_result(result)
        return result

    results = await asyncio.gather(*(one(c) for c in cases))
    return Run(
        harness=harness.name,
        prompt_hash=harness.prompt.hash,
        model=target,
        judge_model=judge_model,
        started_at=started,
        finished_at=_now(),
        results=list(results),
    )


async def run_matrix(
    harness: Harness,
    targets: list[ModelRef],
    providers: dict[str, Provider],
    client: ChatClient,
    judge_model: ModelRef | None = None,
    settings: RunSettings = RunSettings(),
    on_result: Callable[[ModelRef, CaseResult], None] | None = None,
) -> list[Run]:
    runs: list[Run] = []
    for target in targets:
        cb = (lambda r, t=target: on_result(t, r)) if on_result else None
        runs.append(
            await run_harness(
                harness, target, providers, client, judge_model, settings, on_result=cb
            )
        )
    return runs
