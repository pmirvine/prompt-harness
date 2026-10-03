import typer

app = typer.Typer(help="PromptHarness: prompt regression testing.")


@app.callback(invoke_without_command=True)
def main() -> None:
    """Placeholder entry point (replaced in later tasks)."""
    typer.echo("PromptHarness")
