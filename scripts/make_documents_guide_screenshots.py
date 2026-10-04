"""Regenerate the screenshots used by docs/working-with-documents.md.

Like make_getting_started_screenshots.py, this drives the real TUI against a REAL
OpenAI-compatible server (for example LM Studio) through the whole guide: one case with the
three sample invoices in docs/sample-documents/, a vague first prompt, two improved prompts,
checks, prompt history, a custom "Docs as" name, a judge, saving, a regression run on a second
model, the Compare view and the export dialog. It uses a temporary data directory, so your own
providers, harnesses and runs are never touched. It prints every model answer as it goes.

    uv run python scripts/make_documents_guide_screenshots.py \\
        --base-url http://localhost:1234/v1 --model prism-ml/bonsai-27b \\
        --second-model google/gemma-4-31b-qat

Requirements: a server listening at --base-url with both models available (they must accept
images; LM Studio may load them on demand), and `rsvg-convert` (`brew install librsvg`) to
produce the PNGs. The second model is also the judge.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_screenshots import save as _save_png  # noqa: E402

OUT = ROOT / "docs" / "screenshots" / "documents-guide"
WIDTH = 125
BASE_HEIGHT = 46  # the Studio shots use this height too, so the output log never re-wraps
SHOTS = (
    "01-case-form", "02-vague-result", "03-documents-in-template", "04-json-answer",
    "05-checks", "06-checks-pass", "07-history-back", "08-wrong-name", "09-judge-prompt",
    "10-judge-pass", "11-save", "12-regression", "13-matrix", "14-compare", "15-export",
)


def save(app, name: str, out: Path) -> None:
    """Write the PNG, then shrink it to a 64-colour palette when ImageMagick is installed."""
    _save_png(app, name, out)
    png = out / f"{name}.png"
    if png.exists() and shutil.which("magick"):
        subprocess.run(["magick", str(png), "+dither", "-colors", "64", "-strip", str(png)],
                       check=True)

# The text the guide tells the reader to type, kept in one place.
CASE_NAME = "invoices"
CASE_INPUT = "How much do I owe on these invoices, and when is the first one due?"
DOCS = ("docs/sample-documents/invoice-1041.docx, docs/sample-documents/invoice-1042.pdf, "
        "docs/sample-documents/invoice-1043.png")
TEMPLATE_V1 = "{{ input }}"
TEMPLATE_V2 = """{{ input }}

{% for d in documents %}{{ d.name }}:
{{ d.text }}

{% endfor %}"""
SYSTEM_V3 = """You read invoices and answer with a single JSON object and nothing else: no Markdown, no code fences, no explanation.
Use exactly these keys:
- "total": the sum of all the invoice totals in EUR, as a number (for example 12.5)
- "earliest_due": the earliest due date, as a string in the form YYYY-MM-DD
Read every document, including any images."""
TEMPLATE_DOC = TEMPLATE_V2.replace("in documents", "in doc")
MUST_INCLUDE = "400.5\n2026-11-01"
SCHEMA = """{
  "type": "object",
  "required": ["total", "earliest_due"],
  "properties": {
    "total": {"type": "number"},
    "earliest_due": {"type": "string", "pattern": "^\\\\d{4}-\\\\d{2}-\\\\d{2}$"}
  }
}"""
JUDGE_PROMPT = ("The answer must give the combined total of {{ doc[0].name }}, {{ doc[1].name }} "
                "and {{ doc[2].name }} in EUR, and the due date of whichever of them is due "
                "first. Check both figures against the documents.")
HARNESS = "invoice-totals"
DESCRIPTION = "Total and earliest due date of three invoices (Word, PDF, image)"


class _Done(Exception):
    """Raised once every screenshot requested with --only has been written."""


def _server_models(base_url: str) -> list[str]:
    with urllib.request.urlopen(base_url.rstrip("/") + "/models", timeout=30) as resp:
        return sorted(m["id"] for m in json.load(resp)["data"])


async def flow(db_file: Path, export_dir: Path, args, only: set[str] | None) -> None:
    from promptharness.core.db import Database
    from promptharness.core.models import Provider
    from promptharness.tui.app import PromptHarnessApp

    db = Database(db_file)
    # The reader added this provider in the getting-started guide. The longer timeout is only
    # here so a slow first load on the screenshot machine cannot spoil a run.
    db.save_provider(Provider(name=args.provider, base_url=args.base_url, api_key_env="",
                              timeout=300))
    db.save_models(args.provider, _server_models(args.base_url))
    app = PromptHarnessApp(db=db)
    remaining = set(only) if only is not None else None
    try:
        await _drive(app, args=args, only=only, remaining=remaining, export_dir=export_dir)
    except _Done:
        pass


async def _drive(app, *, args, only, remaining, export_dir) -> None:
    from textual.widgets import Button, Checkbox, Input, Select, SelectionList, TextArea

    from promptharness.tui.compare import CompareScreen
    from promptharness.tui.matrix import MatrixScreen

    model = f"{args.provider}:{args.model}"
    second = f"{args.provider}:{args.second_model}"

    async with app.run_test(size=(WIDTH, BASE_HEIGHT)) as pilot:

        async def settle():
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

        async def shot(name: str, height: int, after_resize=None):
            if only is not None and name not in only:
                return  # the flow still runs; only the requested images are rewritten
            await pilot.resize_terminal(WIDTH, height)
            await pilot.pause()
            if after_resize is not None:  # scroll positions must be set at the final size
                after_resize()
                await pilot.pause()
            if app.screen is app.default_screen:
                studio.query_one("#output").scroll_end(animate=False)  # newest result
                await pilot.pause()
            save(app, name, OUT)
            await pilot.resize_terminal(WIDTH, BASE_HEIGHT)
            await pilot.pause()
            if remaining is not None:
                remaining.discard(name)
                if not remaining:
                    raise _Done

        studio = app.query_one("#studio-pane")

        def q(selector, cls=None):
            return app.screen.query_one(selector, cls) if cls else app.screen.query_one(selector)

        async def choose(select_id: str, value: str):
            """Open a Select menu with a click and pick `value` with the arrow keys."""
            sel = studio.query_one(select_id, Select)
            values = [v for _, v in sel._options]  # index 0 is the blank prompt
            await pilot.click(select_id)
            await pilot.pause()
            await pilot.press("home")
            for _ in range(values.index(value)):
                await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            assert sel.value == value, (select_id, sel.value)

        async def run_case(key: str = "r"):
            """Press `key`, wait for the run, print what it added to the output log."""
            start = len(studio._log)
            await pilot.press(key)
            await settle()
            while studio._active_runs:
                await asyncio.sleep(0.2)
            await settle()
            print("\n".join(studio._log[start:]), flush=True)

        async def edit_case():
            await pilot.click(studio.query_one("#cases").children[0])  # opens the case form
            await settle()

        await settle()
        await pilot.press("2")  # Studio
        await settle()
        await choose("#model", model)

        # Step 1: the case with three documents.
        await pilot.click("#cases")
        await pilot.press("n")
        await settle()
        q("#case-name", Input).value = CASE_NAME
        q("#case-input", TextArea).text = CASE_INPUT
        q("#case-docs", Input).value = DOCS
        await pilot.pause()
        await shot("01-case-form", 27)
        q("#case-submit", Button).press()
        await settle()

        # Step 2: the vague first prompt (empty system prompt, default template).
        assert studio.query_one("#template", TextArea).text == TEMPLATE_V1
        await run_case("r")
        await shot("02-vague-result", BASE_HEIGHT)

        # Step 3: put the text documents into the template; run with ctrl+r from the editor.
        studio.query_one("#template", TextArea).text = TEMPLATE_V2
        await pilot.click("#template")
        await run_case("ctrl+r")
        await shot("03-documents-in-template", BASE_HEIGHT)

        # Step 4: ask for JSON in the system prompt.
        studio.query_one("#system", TextArea).text = SYSTEM_V3
        await pilot.click("#system")
        await run_case("ctrl+r")
        await shot("04-json-answer", BASE_HEIGHT)

        # Step 5: checks in the case form.
        await edit_case()
        q("#must-include", TextArea).text = MUST_INCLUDE
        q("#json-output", Checkbox).value = True
        q("#json-schema", TextArea).text = SCHEMA
        await pilot.pause()
        form = q("#case-form")
        label = form.children[form.children.index(q("#must-include")) - 1]  # "Must include"
        await shot("05-checks", 44,
                   after_resize=lambda: form.scroll_to_widget(label, animate=False, top=True))
        q("#case-submit", Button).press()
        await settle()
        await run_case("r")
        await shot("06-checks-pass", BASE_HEIGHT)

        # Step 6: prompt history; step back to the prose prompt, run it against the checks.
        await pilot.press("ctrl+z")
        await pilot.pause()
        assert studio.query_one("#system", TextArea).text == ""
        assert studio.query_one("#template", TextArea).text == TEMPLATE_V2
        await run_case("r")
        await shot("07-history-back", BASE_HEIGHT)
        await pilot.press("ctrl+y")
        await pilot.pause()
        assert studio.query_one("#system", TextArea).text == SYSTEM_V3
        await run_case("r")

        # Step 7: a custom documents name, first with the template still using `documents`.
        await pilot.click("#documents_name")
        await pilot.press("d", "o", "c")
        assert studio.query_one("#documents_name", Input).value == "doc"
        await run_case("ctrl+r")
        await shot("08-wrong-name", BASE_HEIGHT)
        studio.query_one("#template", TextArea).text = TEMPLATE_DOC
        await pilot.click("#template")
        await run_case("ctrl+r")

        # Step 8: a judge prompt that names the documents, and a judge model.
        await edit_case()
        q("#judge-prompt", TextArea).text = JUDGE_PROMPT
        await pilot.pause()
        await shot("09-judge-prompt", 34,
                   after_resize=lambda: q("#case-form").scroll_end(animate=False))
        q("#case-submit", Button).press()
        await settle()
        await choose("#judge", second)
        await run_case("ctrl+r")
        await shot("10-judge-pass", BASE_HEIGHT)

        # Step 9: save as a harness.
        await pilot.press("ctrl+s")
        await settle()
        q("#save-name", Input).value = HARNESS
        q("#save-description", Input).value = DESCRIPTION
        await pilot.pause()
        await shot("11-save", 22)
        q("#save-submit", Button).press()
        await settle()

        # Step 10: regression run on both models, judged by the second one.
        await pilot.press("1")
        await settle()
        await pilot.press("m")
        await settle()
        models = q("#regression-models", SelectionList)
        options = [models.get_option_at_index(i).value for i in range(models.option_count)]
        models.focus()
        for ref in (model, second):
            models.highlighted = options.index(ref)
            await pilot.press("space")
        q("#regression-judge", Select).value = second
        await pilot.pause()
        assert sorted(models.selected) == sorted([model, second]), models.selected
        await shot("12-regression", 30)
        q("#regression-run", Button).press()
        await settle()
        for _ in range(12000):  # up to 20 minutes for slow models
            screen = app.screen
            if isinstance(screen, MatrixScreen) and not screen.running:
                break
            await asyncio.sleep(0.1)
        await settle()
        matrix = app.screen
        for (case, key), r in matrix.results.items():
            print(f"matrix {case} x {key}: {r.status} {r.latency_ms} ms {r.output!r} "
                  f"{[(c.name, c.passed, c.reason) for c in r.checks]} {r.warnings} {r.error}",
                  flush=True)
        await shot("13-matrix", 9)

        await pilot.press("c")
        await settle()
        assert isinstance(app.screen, CompareScreen)
        await shot("14-compare", 20)
        await pilot.press("escape", "escape")
        await settle()

        # Step 11: export with the documents inlined (written to a temporary folder).
        os.chdir(export_dir)
        await pilot.press("e")
        await settle()
        await pilot.click("#export-inline")
        await pilot.pause()
        assert q("#export-inline", Checkbox).value
        await shot("15-export", 24)
        q("#export-submit", Button).press()
        await settle()
        text = (export_dir / f"{HARNESS}.yaml").read_text(encoding="utf-8")
        print(f"exported {len(text)} characters", flush=True)
        if args.keep_export:
            shutil.copy(export_dir / f"{HARNESS}.yaml", args.keep_export)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default="http://localhost:1234/v1")
    ap.add_argument("--model", default="prism-ml/bonsai-27b")
    ap.add_argument("--second-model", default="google/gemma-4-31b-qat")
    ap.add_argument("--provider", default="lmstudio")
    ap.add_argument(
        "--only",
        help="comma-separated screenshot names to rewrite (default: all), e.g. 13-matrix",
    )
    ap.add_argument("--keep-export", help="also copy the exported YAML file to this path")
    args = ap.parse_args()
    only = set(args.only.split(",")) if args.only else None
    unknown = sorted(only - set(SHOTS)) if only else []
    if unknown:
        ap.error(f"unknown screenshot name(s): {', '.join(unknown)}; choose from {', '.join(SHOTS)}")
    if args.keep_export:
        args.keep_export = Path(args.keep_export).resolve()  # before the chdir below

    tmp = tempfile.mkdtemp(prefix="promptharness-docs-")
    os.environ["PROMPTHARNESS_HOME"] = tmp
    cwd = os.getcwd()
    os.chdir(ROOT)  # the guide types the sample paths relative to a clone of the repository
    try:
        export_dir = Path(tmp) / "export"
        export_dir.mkdir()
        asyncio.run(flow(Path(tmp) / "docs.db", export_dir, args, only))
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
