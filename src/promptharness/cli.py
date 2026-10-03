from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable, Optional

import typer

from promptharness import paths
from promptharness.core import portable
from promptharness.core.client import ChatClient, OpenAIChatClient
from promptharness.core.db import Database
from promptharness.core.models import CaseResult, ModelRef, Provider, Run
from promptharness.core.portable import PortableError
from promptharness.core.runner import RunSettings, run_harness

app = typer.Typer(help="PromptHarness: prompt regression testing.")
provider_app = typer.Typer(help="Manage providers.")
app.add_typer(provider_app, name="provider")

client_factory: Callable[[], ChatClient] = OpenAIChatClient

_FAILING = {"fail", "error", "judge_error"}
_MAX_TOKENS_PARAMS = ("max_tokens", "max_completion_tokens")


def _usage_error(msg: str) -> typer.Exit:
    typer.echo(f"Error: {msg}", err=True)
    return typer.Exit(2)


def _parse_ref(spec: str) -> ModelRef:
    try:
        ref = ModelRef.parse(spec)
    except ValueError as e:
        raise _usage_error(str(e)) from e
    if not ref.provider or not ref.model:
        raise _usage_error(f"invalid model reference (expected 'provider:model'): {spec!r}")
    return ref


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Launch the TUI when no subcommand is given."""
    if ctx.invoked_subcommand is None:
        from promptharness.tui.app import run_app

        run_app()


def format_table(runs: list[Run], case_names: list[str]) -> str:
    headers = ["case"] + [str(r.model) for r in runs]
    rows = []
    for name in case_names:
        row = [name]
        for r in runs:
            res = next((x for x in r.results if x.case_name == name), None)
            row.append(res.status if res else "-")
        rows.append(row)
    widths = [max(len(row[i]) for row in [headers] + rows) for i in range(len(headers))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip() for row in [headers] + rows]
    return "\n".join(lines)


_STATUSES = ("pass", "fail", "error", "judge_error", "manual")


def _detail(res: CaseResult) -> tuple[str, str | None]:
    """Return (detail, warning-used-as-detail) explaining a non-pass result."""
    if res.error:
        return res.error, None
    failed = next((c for c in res.checks if not c.passed), None)
    if failed is not None:
        return (f"{failed.name}: {failed.reason}" if failed.reason else failed.name), None
    if res.status == "judge_error":
        w = next((w for w in res.warnings if "judge" in w), None)
        return (w or "judge unavailable"), w
    if res.status == "manual":
        return "no automated checks; review manually", None
    return "", None


def format_details(runs: list[Run], case_names: list[str]) -> str:
    """Per-cell failure reasons and warnings, then a status summary line."""
    lines: list[str] = []
    counts = dict.fromkeys(_STATUSES, 0)
    for r in runs:
        for name in case_names:
            res = next((x for x in r.results if x.case_name == name), None)
            if res is None:
                continue
            counts[res.status] = counts.get(res.status, 0) + 1
            label = f"{name} × {r.model}"
            used: str | None = None
            if res.status != "pass":
                detail, used = _detail(res)
                lines.append(f"{label}: {res.status}" + (f" — {detail}" if detail else ""))
            for w in res.warnings:
                if w is not used:
                    lines.append(f"{label}: warning: {w}")
    lines.append(", ".join(f"{counts[s]} {s}" for s in _STATUSES))
    return "\n".join(lines)


@app.command()
def run(
    harness: str = typer.Argument(..., help="Harness name"),
    model: list[str] = typer.Option(..., "--model", help="provider:model (repeatable)"),
    judge: Optional[str] = typer.Option(None, "--judge", help="provider:model for judging"),
    concurrency: int = typer.Option(2, "--concurrency", min=1),
    case: Optional[str] = typer.Option(None, "--case", help="Run only this case"),
) -> None:
    """Run a harness against one or more models."""
    targets = [_parse_ref(m) for m in model]
    judge_ref = _parse_ref(judge) if judge else None
    db = Database(paths.db_path())
    try:
        h = db.get_harness(harness)
        if h is None:
            raise _usage_error(f"harness {harness!r} not found")
        if case is not None and all(c.name != case for c in h.cases):
            raise _usage_error(f"unknown case {case!r} in harness {harness!r}")
        providers = {p.name: p for p in db.list_providers()}
        client = client_factory()
        settings = RunSettings(concurrency=concurrency)

        async def go() -> list[Run]:
            return [
                await run_harness(
                    h, t, providers, client, judge_ref, settings, only_case=case
                )
                for t in targets
            ]

        runs = asyncio.run(go())
        for r in runs:
            r.id = db.save_run(r)
    finally:
        db.close()
    names = [c.name for c in h.cases if case is None or c.name == case]
    typer.echo(format_table(runs, names))
    typer.echo(format_details(runs, names))
    if any(res.status in _FAILING for r in runs for res in r.results):
        raise typer.Exit(1)


@app.command()
def export(
    harness: str = typer.Argument(...),
    format: str = typer.Option("yaml", "--format", help="yaml or json"),
    inline_documents: bool = typer.Option(False, "--inline-documents"),
    out: Optional[Path] = typer.Option(None, "--out"),
) -> None:
    """Export a harness to YAML or JSON."""
    if format not in ("yaml", "json"):
        raise _usage_error(f"unknown format {format!r} (expected yaml or json)")
    db = Database(paths.db_path())
    try:
        text = portable.export_harness(db, harness, format, inline_documents)  # type: ignore[arg-type]
    except PortableError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from e
    finally:
        db.close()
    if out is None:
        typer.echo(text)
    else:
        out.write_text(text, encoding="utf-8")
        typer.echo(f"Exported {harness} to {out}")


@app.command(name="import")
def import_(
    path: Path = typer.Argument(...),
    overwrite: bool = typer.Option(False, "--overwrite"),
) -> None:
    """Import a harness from a YAML or JSON file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        typer.echo(f"Error: cannot read {path}: {e}", err=True)
        raise typer.Exit(1) from e
    db = Database(paths.db_path())
    try:
        h = portable.import_harness(db, text, overwrite=overwrite)
    except PortableError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from e
    finally:
        db.close()
    typer.echo(f"Imported {h.name}")


@provider_app.command("add")
def provider_add(
    name: str = typer.Argument(...),
    base_url: str = typer.Option(..., "--base-url"),
    api_key_env: str = typer.Option(..., "--api-key-env", help="Name of env var holding the key"),
    max_tokens_param: str = typer.Option(
        "max_tokens",
        "--max-tokens-param",
        help="Request field for the token limit: max_tokens or max_completion_tokens",
    ),
) -> None:
    """Add or update a provider (the key itself is never stored)."""
    if max_tokens_param not in _MAX_TOKENS_PARAMS:
        raise _usage_error(
            f"invalid --max-tokens-param {max_tokens_param!r} "
            f"(expected {' or '.join(_MAX_TOKENS_PARAMS)})"
        )
    db = Database(paths.db_path())
    try:
        db.save_provider(
            Provider(
                name=name,
                base_url=base_url,
                api_key_env=api_key_env,
                max_tokens_param=max_tokens_param,  # type: ignore[arg-type]
            )
        )
    finally:
        db.close()
    typer.echo(f"Saved provider {name}")


@provider_app.command("list")
def provider_list() -> None:
    """List configured providers."""
    db = Database(paths.db_path())
    try:
        providers = db.list_providers()
    finally:
        db.close()
    for p in providers:
        state = "" if p.enabled else " (disabled)"
        typer.echo(f"{p.name}  {p.base_url}  {p.api_key_env}{state}")
