"""Regenerate the README screenshots (docs/screenshots/*.png).

Drives the real Textual screens headlessly with Textual's Pilot and exports each
one as an SVG, then converts it to PNG with `rsvg-convert` (`brew install librsvg`), which is
required for the PNGs the README uses; without it the SVGs are kept instead. Nothing here talks to
a network or touches your real data: it uses a temporary data directory and
replays recorded model outputs through the real runner.

    uv run python scripts/make_screenshots.py

The recorded outputs, token counts and latencies below come from real runs of the
quickstart harness against models served by LM Studio.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
# Terminal size (columns, rows) per screenshot, sized to the content so the images stay readable.
SIZES = {
    "harnesses": (110, 11),
    "providers": (110, 12),
    "runs": (118, 13),
    "studio": (165, 40),
    "matrix": (130, 11),
    "result-detail": (100, 15),
    "compare": (130, 22),
    "result-warning": (110, 13),
}
# Real times of the recorded runs (UTC); the Runs tab shows them in local time.
RUN_TIMES = {
    ("summarize-example", "openai:gpt-4o-mini"): "2026-10-03T16:20:00+00:00",
    ("summarize-example", "prism-ml/bonsai-27b"): "2026-10-03T16:41:16+00:00",
    ("quickstart", "prism-ml/bonsai-27b"): "2026-10-03T16:52:30+00:00",
    ("quickstart", "openai/gpt-oss-20b"): "2026-10-03T17:34:37+00:00",
    ("quickstart", "google/gemma-4-31b-qat"): "2026-10-03T17:34:48+00:00",
}

# (output, prompt_tokens, completion_tokens, latency_ms) per model and case.
RECORDED = {
    "openai/gpt-oss-20b": {
        "capital-of-france": ("Paris", 106, 17, 1334),
        "extract-person-json": ('{"name":"Ada Lovelace","age":36}', 136, 34, 566),
        "three-colors": ("red, blue, green", 109, 30, 505),
    },
    "google/gemma-4-31b-qat": {
        "capital-of-france": ("Paris", 48, 51, 3830),
        "extract-person-json": ('```json\n{\n  "name": "Ada Lovelace",\n  "age": 36\n}\n```', 81, 146, 8664),
        "three-colors": ("red, blue, green", 51, 97, 5804),
    },
    "prism-ml/bonsai-27b": {
        "capital-of-france": ("\n\nParis", 47, 162, 6569),
        "extract-person-json": ('\n\n{\n  "name": "Ada Lovelace",\n  "age": 36\n}', 80, 293, 11498),
        "three-colors": ("\n\nred, blue, green", 50, 484, 14170),
        "one-sentence-summary": ("", 82, 299, 11319),
        "json-extraction": ('\n\n{"city": "Paris", "year": 2024}', 72, 282, 10947),
    },
}
# Warnings the real client attaches when a reasoning model runs out of tokens.
RECORDED_WARNINGS = {
    ("prism-ml/bonsai-27b", "one-sentence-summary"): [
        "empty answer: the model used its token limit after 299 completion tokens "
        "on reasoning before answering; raise max_tokens"
    ],
}
# Which case a rendered prompt belongs to (the replay client matches on the input text).
NEEDLES = {
    "capital of France": "capital-of-france",
    "Ada Lovelace": "extract-person-json",
    "three different colors": "three-colors",
    "library": "one-sentence-summary",
    "Paris hosted": "json-extraction",
}


class ReplayClient:
    """ChatClient that replays recorded outputs instead of calling a server."""

    async def chat(self, provider, model, messages, params):
        from promptharness.core.client import ChatResult

        user = messages[-1]["content"]
        case = next(name for needle, name in NEEDLES.items() if needle in user)
        out, ptok, ctok, ms = RECORDED[model][case]
        return ChatResult(
            text=out,
            prompt_tokens=ptok,
            completion_tokens=ctok,
            latency_ms=ms,
            request={"model": model, "messages": messages},
            response={},
            warnings=list(RECORDED_WARNINGS.get((model, case), [])),
        )

    async def list_models(self, provider):
        return []


def seed(db):
    from promptharness.core.models import ModelRef, Provider
    from promptharness.core.portable import import_harness
    from promptharness.core.runner import run_harness

    providers = {
        "lmstudio": Provider(name="lmstudio", base_url="http://localhost:1234/v1", api_key_env=""),
        "openai": Provider(name="openai", base_url="https://api.openai.com/v1", api_key_env="OPENAI_API_KEY"),
        "ollama": Provider(name="ollama", base_url="http://localhost:11434/v1", api_key_env=""),
    }
    for p in providers.values():
        db.save_provider(p)
    db.save_models("lmstudio", [
        "openai/gpt-oss-20b", "google/gemma-4-31b-qat", "prism-ml/bonsai-27b",
        "google/gemma-4-31b", "qwen/qwen3.8-27b",
    ])
    db.save_models("openai", ["gpt-4o-mini"])
    db.save_models("ollama", ["llama3.2"])

    for name in ("quickstart.harness.yaml", "summarize.harness.yaml"):
        import_harness(db, (ROOT / "examples" / name).read_text(encoding="utf-8"))

    # The imported example's accepted outputs become a stored run; give it a real-looking time too.
    imported = db.list_runs("summarize-example")[0]
    db.conn.execute(
        "UPDATE runs SET started_at=?, finished_at=? WHERE id=?",
        (RUN_TIMES[("summarize-example", "openai:gpt-4o-mini")],) * 2 + (imported.id,),
    )
    db.conn.commit()

    async def go():
        client = ReplayClient()
        quick = db.get_harness("quickstart")
        for model in ("openai/gpt-oss-20b", "google/gemma-4-31b-qat", "prism-ml/bonsai-27b"):
            ref = ModelRef(provider="lmstudio", model=model)
            run = await run_harness(quick, ref, providers, client)
            run.started_at = run.finished_at = RUN_TIMES[("quickstart", model)]
            run_id = db.save_run(run)
            if model == "openai/gpt-oss-20b":
                db.set_accepted("quickstart", ref, run_id)
        summ = db.get_harness("summarize-example")
        ref = ModelRef(provider="lmstudio", model="prism-ml/bonsai-27b")
        run = await run_harness(summ, ref, providers, client)
        run.started_at = run.finished_at = RUN_TIMES[("summarize-example", "prism-ml/bonsai-27b")]
        db.save_run(run)

    asyncio.run(go())


def save(app, name: str, out: Path = OUT) -> None:
    out.mkdir(parents=True, exist_ok=True)
    svg = out / f"{name}.svg"
    svg.write_text(app.export_screenshot(title="PromptHarness"), encoding="utf-8")
    if shutil.which("rsvg-convert"):
        subprocess.run(
            ["rsvg-convert", "--zoom", "2", "-o", str(out / f"{name}.png"), str(svg)], check=True
        )
        svg.unlink()  # the PNG is what the README uses; keep the SVG only if it can't be converted
    print("wrote", name)


async def shoot(db_file: Path):
    from promptharness.core.db import Database
    from promptharness.tui.app import PromptHarnessApp
    from promptharness.tui.matrix import MatrixScreen

    # Stored runs are plain data, so read them once up front. Each app gets its own
    # connection because the app closes its database when it exits.
    reader = Database(db_file)
    quick = reader.list_runs("quickstart")
    summarize = reader.list_runs("summarize-example")
    reader.close()

    async def session(name, steps):
        """Start a fresh app at this screenshot's size, run `steps`, and save the result."""
        app = PromptHarnessApp(db=Database(db_file), client=ReplayClient())
        async with app.run_test(size=SIZES[name]) as pilot:

            async def settle():
                await pilot.pause()
                await app.workers.wait_for_complete()
                await pilot.pause()

            await settle()
            await steps(app, pilot, settle)
            save(app, name)

    def by_model(runs, *models):
        return [next(r for r in runs if r.model.model == m) for m in models]

    async def harnesses(app, pilot, settle):
        await pilot.press("1")
        await settle()

    async def providers(app, pilot, settle):
        await pilot.press("4")
        await settle()

    async def runs_tab(app, pilot, settle):
        await pilot.press("3")
        await settle()

    async def studio(app, pilot, settle):
        # Open the quickstart harness from the Harnesses tab and run every case.
        await pilot.press("1")
        await settle()
        await pilot.press("enter")
        await settle()
        await pilot.press("R")
        await settle()
        await settle()

    async def matrix(app, pilot, settle):
        trio = by_model(quick, "openai/gpt-oss-20b", "google/gemma-4-31b-qat", "prism-ml/bonsai-27b")
        app.push_screen(MatrixScreen.from_runs(trio))
        await settle()

    async def result_detail(app, pilot, settle):
        trio = by_model(quick, "openai/gpt-oss-20b", "google/gemma-4-31b-qat", "prism-ml/bonsai-27b")
        app.push_screen(MatrixScreen.from_runs(trio))
        await settle()
        # Cursor starts on the case-name column: down to extract-person-json, right x2 to Gemma.
        await pilot.press("down", "right", "right", "enter")
        await settle()

    async def compare(app, pilot, settle):
        pair = by_model(quick, "google/gemma-4-31b-qat", "prism-ml/bonsai-27b")
        app.push_screen(MatrixScreen.from_runs(pair))
        await settle()
        await pilot.press("down", "c")  # compare extract-person-json; accepted run is first
        await settle()

    async def result_warning(app, pilot, settle):
        trunc = [r for r in summarize if r.model.model == "prism-ml/bonsai-27b"]
        app.push_screen(MatrixScreen.from_runs(trunc))
        await settle()
        await pilot.press("right", "enter")
        await settle()

    await session("harnesses", harnesses)
    await session("providers", providers)
    await session("runs", runs_tab)
    await session("studio", studio)
    await session("matrix", matrix)
    await session("result-detail", result_detail)
    await session("compare", compare)
    await session("result-warning", result_warning)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="promptharness-shots-")
    os.environ["PROMPTHARNESS_HOME"] = tmp
    try:
        from promptharness.core.db import Database

        db_file = Path(tmp) / "shots.db"
        db = Database(db_file)
        seed(db)
        db.close()
        asyncio.run(shoot(db_file))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
