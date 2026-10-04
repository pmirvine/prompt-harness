"""Regenerate the screenshots used by docs/getting-started.md.

Unlike make_screenshots.py, this drives the real TUI through the whole first-run flow
against a REAL OpenAI-compatible server (for example LM Studio), so the guide is
verified end to end. It uses a temporary data directory, so your own providers, harnesses
and runs are never touched.

    uv run python scripts/make_getting_started_screenshots.py \\
        --base-url http://localhost:1234/v1 --model prism-ml/bonsai-27b

Requirements: a server listening at --base-url with --model available (it may be loaded on
demand), and `rsvg-convert` (`brew install librsvg`) to produce the PNGs.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_screenshots import save  # noqa: E402

OUT = ROOT / "docs" / "screenshots" / "getting-started"
WIDTH = 125


class _Done(Exception):
    """Raised once every screenshot requested with --only has been written."""


async def flow(
    db_file: Path, base_url: str, model: str, provider: str, only: set[str] | None = None
) -> None:
    from promptharness.core.db import Database
    from promptharness.tui.app import PromptHarnessApp

    app = PromptHarnessApp(db=Database(db_file))
    remaining = set(only) if only is not None else None
    try:
        await _drive(app, pilot_size=(WIDTH, 12), provider=provider, base_url=base_url, model=model,
                     only=only, remaining=remaining)
    except _Done:
        pass


async def _drive(app, *, pilot_size, provider, base_url, model, only, remaining) -> None:
    from textual.widgets import Button, Input, SelectionList

    from promptharness.tui.matrix import MatrixScreen

    async with app.run_test(size=pilot_size) as pilot:

        async def settle():
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

        async def shot(name: str, height: int):
            if only is not None and name not in only:
                return  # the flow still runs; only the requested images are rewritten
            await pilot.resize_terminal(WIDTH, height)
            await pilot.pause()
            save(app, name, OUT)
            if remaining is not None:
                remaining.discard(name)
                if not remaining:
                    raise _Done

        await settle()
        await shot("01-first-launch", 12)

        # Providers tab: add the server.
        await pilot.press("4")
        await settle()
        await pilot.press("a")
        await settle()
        app.screen.query_one("#name", Input).value = provider
        app.screen.query_one("#base_url", Input).value = base_url
        await pilot.pause()
        await shot("02-add-provider", 30)

        app.screen.query_one("#submit", Button).press()
        await settle()
        await pilot.press("t")  # test the connection and fetch the model list
        await settle()
        await shot("03-provider-added", 14)

        # Harnesses tab: import the starter harness.
        await pilot.press("1")
        await settle()
        await pilot.press("i")
        await settle()
        app.screen.query_one("#import-path", Input).value = "example:quickstart"
        await pilot.pause()
        await shot("04-import-harness", 17)

        app.screen.query_one("#import-submit", Button).press()
        await settle()
        await shot("05-harness-imported", 12)

        # Regression run: pick the model and run.
        await pilot.press("m")
        await settle()
        models = app.screen.query_one(SelectionList)
        models.select(f"{provider}:{model}")
        # Put the cursor on the chosen model so the highlighted row and the ticked box agree.
        models.highlighted = [models.get_option_at_index(i).value for i in range(models.option_count)].index(
            f"{provider}:{model}"
        )
        await pilot.pause()
        await shot("06-choose-model", 28)

        app.screen.query_one("#regression-run", Button).press()
        await settle()
        for _ in range(6000):  # up to 10 minutes for a slow model
            screen = app.screen
            if isinstance(screen, MatrixScreen) and not screen.running:
                break
            await asyncio.sleep(0.1)
        await settle()
        await shot("07-results", 13)

        await pilot.press("down", "right", "enter")  # extract-person-json x the model
        await settle()
        await shot("08-result-detail", 15)

        await pilot.press("escape", "escape")
        await settle()
        await pilot.press("3")
        await settle()
        await shot("09-runs", 12)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default="http://localhost:1234/v1")
    ap.add_argument("--model", default="prism-ml/bonsai-27b")
    ap.add_argument("--provider", default="lmstudio")
    ap.add_argument(
        "--only",
        help="comma-separated screenshot names to rewrite (default: all), e.g. 04-import-harness",
    )
    args = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="promptharness-gs-")
    os.environ["PROMPTHARNESS_HOME"] = tmp
    try:
        only = set(args.only.split(",")) if args.only else None
        asyncio.run(flow(Path(tmp) / "gs.db", args.base_url, args.model, args.provider, only))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
