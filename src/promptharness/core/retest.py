"""Re-test on replacement: re-run a past run's harness against a different model.

The re-test uses the harness's *current* prompt; `prompt_changed` tells callers whether
that prompt differs from the one the original run used.
"""

from __future__ import annotations

from collections.abc import Callable

from promptharness.core.client import ChatClient
from promptharness.core.db import Database
from promptharness.core.models import CaseResult, ModelRef, Provider, Run
from promptharness.core.runner import RunSettings, run_harness

__all__ = ["prompt_changed", "retest", "retest_config"]


def retest_config(run: Run, new_model: ModelRef) -> dict:
    """The run's configuration with the target model swapped (judge model kept)."""
    if new_model == run.model:
        raise ValueError(f"{new_model} is the model of the original run; pick a different one")
    return {"harness": run.harness, "target": new_model, "judge_model": run.judge_model}


def prompt_changed(db: Database, run: Run) -> bool:
    """True if the run's harness still exists and its prompt differs from the run's."""
    h = db.get_harness(run.harness)
    return h is not None and h.prompt.hash != run.prompt_hash


async def retest(
    db: Database,
    run: Run,
    new_model: ModelRef,
    providers: dict[str, Provider],
    client: ChatClient,
    settings: RunSettings | None = None,
    on_result: Callable[[CaseResult], None] | None = None,
) -> Run:
    """Run `run`'s harness (current prompt) against `new_model`, save and return the run.

    Raises ValueError for the same model or a harness that no longer exists. Provider and
    model failures become per-case error results (see core.runner)."""
    cfg = retest_config(run, new_model)
    harness = db.get_harness(cfg["harness"])
    if harness is None:
        raise ValueError(f"harness {run.harness!r} no longer exists; cannot re-test")
    new = await run_harness(
        harness,
        cfg["target"],
        providers,
        client,
        judge_model=cfg["judge_model"],
        settings=settings or RunSettings(),
        on_result=on_result,
    )
    db.save_run(new)
    return new
