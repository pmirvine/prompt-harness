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

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # Tabs live on the default screen; don't switch them underneath a pushed screen.
        if action == "tab" and self.screen is not self.default_screen:
            return False
        return True

    def action_tab(self, name: str) -> None:
        self.query_one(TabbedContent).active = name

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        tab = event.pane.id
        if tab == "harnesses":
            self.query_one(HarnessesPane).refresh_table()
        elif tab == "runs":
            self.query_one(RunsPane).refresh_table()
        elif tab == "studio":
            self.query_one(StudioPane).refresh_models()
        target = {"harnesses": "#harnesses-table", "studio": "#cases",
                  "runs": "#runs-table", "providers": "#providers-table"}.get(tab or "")
        if target is not None:
            self.call_after_refresh(self._focus_if_active, tab, target)

    def _focus_if_active(self, tab: str, selector: str) -> None:
        # Deferred: if the user already switched again, focusing would bounce tabs back.
        if self.query_one(TabbedContent).active == tab:
            self.query_one(selector).focus()

    def on_unmount(self) -> None:
        try:
            self.db.close()
        except Exception:
            pass


def run_app() -> None:
    PromptHarnessApp().run()
