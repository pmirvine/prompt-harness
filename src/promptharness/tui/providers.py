from __future__ import annotations

from urllib.parse import urlsplit

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, DataTable, Input, Label, Select, TextArea

from promptharness.core.client import ClientError
from promptharness.core.models import Provider


MAX_TOKENS_PARAMS = ("max_tokens", "max_completion_tokens")


class ProviderForm(ModalScreen["Provider | None"]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, provider: Provider | None = None) -> None:
        super().__init__()
        self.existing = provider

    def compose(self) -> ComposeResult:
        p = self.existing
        with Vertical(id="form", classes="compact"):
            yield Label("Edit provider" if p else "Add provider")
            yield Label("Name")
            yield Input(p.name if p else "", id="name", disabled=p is not None)
            yield Label("Base URL")
            yield Input(p.base_url if p else "", id="base_url",
                        placeholder="https://api.openai.com/v1")
            yield Label("API key env var NAME (blank for local servers)")
            yield Input(p.api_key_env if p else "", id="api_key_env",
                        placeholder="OPENAI_API_KEY")
            yield Label("Token limit parameter")
            yield Select(
                [(v, v) for v in MAX_TOKENS_PARAMS],
                value=p.max_tokens_param if p else "max_tokens",
                allow_blank=False,
                id="max_tokens_param",
            )
            with Horizontal(id="buttons"):
                yield Button("Save", id="submit", variant="primary")
                yield Button("Cancel", id="cancel")

    @on(Button.Pressed, "#submit")
    def _submit(self) -> None:
        name = self.query_one("#name", Input).value.strip()
        url = self.query_one("#base_url", Input).value.strip()
        env = self.query_one("#api_key_env", Input).value.strip()
        mtp = self.query_one("#max_tokens_param", Select).value
        if not (name and url):
            self.notify("Name and base URL are required", severity="error")
            return
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            self.notify("Base URL must start with http:// or https:// and include a host",
                        severity="error")
            return
        if self.existing is None and self.app.db.get_provider(name) is not None:  # type: ignore[attr-defined]
            self.notify(f"Provider {name!r} already exists", severity="error")
            return
        if self.existing is not None:
            result = self.existing.model_copy(
                update={"base_url": url, "api_key_env": env, "max_tokens_param": mtp}
            )
        else:
            result = Provider(name=name, base_url=url, api_key_env=env,
                              max_tokens_param=mtp)  # type: ignore[arg-type]
        self.dismiss(result)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class ModelsForm(ModalScreen["list[str] | None"]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, provider: str, current: list[str]) -> None:
        super().__init__()
        self.provider_name = provider
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="form"):
            yield Label(f"Models for {self.provider_name} (one per line)")
            yield TextArea("\n".join(self.current), id="models")
            with Horizontal(id="buttons"):
                yield Button("Save", id="submit", variant="primary")
                yield Button("Cancel", id="cancel")

    @on(Button.Pressed, "#submit")
    def _submit(self) -> None:
        text = self.query_one("#models", TextArea).text
        self.dismiss([ln.strip() for ln in text.splitlines() if ln.strip()])

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class ProvidersPane(Widget):
    BINDINGS = [
        Binding("a", "add", "Add"),
        Binding("e", "edit", "Edit"),
        Binding("d", "toggle", "Enable/disable"),
        Binding("t", "test", "Test"),
        Binding("m", "manual", "Models"),
    ]

    def compose(self) -> ComposeResult:
        yield DataTable(id="providers-table", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        t = self.query_one(DataTable)
        t.add_columns("Name", "Base URL", "API key env", "Enabled", "Models")
        self.refresh_table()

    @property
    def db(self):
        return self.app.db  # type: ignore[attr-defined]

    def refresh_table(self) -> None:
        t = self.query_one(DataTable)
        t.clear()
        for p in self.db.list_providers():
            t.add_row(
                p.name,
                p.base_url,
                p.api_key_env,
                "yes" if p.enabled else "no",
                str(len(self.db.list_models(p.name))),
                key=p.name,
            )

    def _selected(self) -> Provider | None:
        t = self.query_one(DataTable)
        if t.row_count == 0:
            return None
        try:
            key = t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value
        except Exception:
            return None
        return self.db.get_provider(key) if key else None

    def _need_selection(self) -> Provider | None:
        p = self._selected()
        if p is None:
            self.notify("No provider selected", severity="warning")
        return p

    def _save(self, p: Provider | None) -> None:
        if p is not None:
            self.db.save_provider(p)
            self.refresh_table()

    def action_add(self) -> None:
        self.app.push_screen(ProviderForm(), self._save)

    def action_edit(self) -> None:
        p = self._need_selection()
        if p:
            self.app.push_screen(ProviderForm(p), self._save)

    def action_toggle(self) -> None:
        p = self._need_selection()
        if p:
            p.enabled = not p.enabled
            self.db.save_provider(p)
            self.refresh_table()

    def action_manual(self) -> None:
        p = self._need_selection()
        if p:
            self._open_manual(p.name)

    def _open_manual(self, name: str) -> None:
        def done(models: list[str] | None) -> None:
            if models is not None:
                self.db.save_models(name, models)
                self.refresh_table()

        self.app.push_screen(ModelsForm(name, self.db.list_models(name)), done)

    def action_test(self) -> None:
        p = self._need_selection()
        if p:
            self.notify(f"Testing {p.name}...")
            self._test(p)

    @work(exclusive=True, group="provider-test")
    async def _test(self, p: Provider) -> None:
        try:
            models = await self.app.client.list_models(p)  # type: ignore[attr-defined]
        except ClientError as e:
            self._failed(p, f"{e.kind}: {e}")
            return
        except Exception as e:  # never crash the UI
            self._failed(p, f"{type(e).__name__}: {e}")
            return
        self.db.save_models(p.name, models)
        self.refresh_table()
        self.notify(f"{p.name}: connected, {len(models)} models")

    def _failed(self, p: Provider, msg: str) -> None:
        self.notify(f"{p.name}: connection failed: {msg}", severity="error", timeout=8)
        self._open_manual(p.name)
