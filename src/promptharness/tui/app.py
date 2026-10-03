from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, TabbedContent, TabPane

from promptharness import paths
from promptharness.core.client import ChatClient, OpenAIChatClient
from promptharness.core.db import Database
from promptharness.tui.harnesses import HarnessesPane
from promptharness.tui.providers import ProvidersPane
from promptharness.tui.runs import RunsPane
from promptharness.tui.studio import StudioPane


class PromptHarnessApp(App):
    TITLE = "PromptHarness"
    CSS_PATH = Path(__file__).with_name("app.tcss")
    BINDINGS = [
        Binding("1", "tab('harnesses')", "Harnesses"),
        Binding("2", "tab('studio')", "Studio"),
        Binding("3", "tab('runs')", "Runs"),
        Binding("4", "tab('providers')", "Providers"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, db: Database | None = None, client: ChatClient | None = None) -> None:
        super().__init__()
        self.db = db if db is not None else Database(paths.db_path())
        self.client = client if client is not None else OpenAIChatClient()

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="harnesses"):
            with TabPane("1 Harnesses", id="harnesses"):
                yield HarnessesPane(id="harnesses-pane")
            with TabPane("2 Studio", id="studio"):
                yield StudioPane(id="studio-pane")
            with TabPane("3 Runs", id="runs"):
                yield RunsPane(id="runs-pane")
            with TabPane("4 Providers", id="providers"):
                yield ProvidersPane(id="providers-pane")
        yield Footer()

    def action_tab(self, name: str) -> None:
        self.query_one(TabbedContent).active = name

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if event.pane.id == "providers":
            self.call_after_refresh(self.query_one("#providers-table").focus)
        elif event.pane.id == "studio":
            self.query_one(StudioPane).refresh_models()
            self.call_after_refresh(self.query_one("#cases").focus)

    def on_unmount(self) -> None:
        try:
            self.db.close()
        except Exception:
            pass


def run_app() -> None:
    PromptHarnessApp().run()
