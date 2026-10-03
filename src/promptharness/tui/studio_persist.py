"""Studio pane behaviour for verdicts, saving as a harness and loading a harness.

A mixin for StudioPane (kept separate to keep studio.py a manageable size).
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Input, ListView, RichLog, Select, TextArea

from promptharness.core.models import Harness, ModelRef
from promptharness.core.status import final_status
from promptharness.tui.studio_modals import ConfirmModal, SaveForm, VerdictModal
from promptharness.tui.studio_support import (
    STATUS_STYLE,
    PromptHistory,
    StudioEntry,
    save_accepted_run,
)


class StudioPersistMixin:
    """Requires the StudioPane attributes (cases, _entries, db, _busy, ...)."""

    # -- verdict ----------------------------------------------------------
    async def action_verdict(self) -> None:
        idx = self._selected_index()
        entry = self._entries.get(self.cases[idx].name) if idx is not None else None
        if entry is None:
            self.notify("No result for the selected case; run it first", severity="warning")
            return

        def done(choice: str | None) -> None:
            if choice is None:
                return
            verdict = {"pass": True, "fail": False, "clear": None}[choice]
            self._apply_verdict(entry, verdict)

        self.app.push_screen(VerdictModal(entry.result.case_name), done)

    def _apply_verdict(self, entry: StudioEntry, verdict: bool | None) -> None:
        r = entry.result
        if r.id is not None:
            try:
                saved = self.db.set_manual_verdict(r.id, verdict)
            except Exception as e:
                self.notify(f"Could not save verdict: {e}", severity="error")
                return
            r.status, r.manual_verdict = saved.status, saved.manual_verdict
        else:
            r.manual_verdict = verdict
            r.status = final_status(r.checks, r.error, entry.judge_error, verdict)
        self._unsaved_results = True
        if r.case_name in self._entries:
            idx = next(i for i, c in enumerate(self.cases) if c.name == r.case_name)
            self._set_label(idx, self._label(self.cases[idx]))
        self._write(Text(f"verdict: {r.case_name} → {r.status.upper()}",
                         style=STATUS_STYLE.get(r.status, "bold")))

    # -- save -----------------------------------------------------------
    async def action_save(self) -> None:
        if self._busy():
            return
        try:
            self.current_prompt()
        except ValueError as e:
            self.notify(str(e), severity="error")
            return

        def chosen(answer: tuple[str, str] | None) -> None:
            if answer is None:
                return
            name, desc = answer
            try:
                exists = self.db.get_harness(name) is not None
            except Exception as e:  # never crash the UI
                self.notify(f"Save failed: {type(e).__name__}: {e}", severity="error")
                return
            if not exists:
                self._save(name, desc)
                return

            def confirmed(yes: bool | None) -> None:
                if yes:
                    self._save(name, desc)

            self.app.push_screen(
                ConfirmModal(f"Harness {name!r} exists. Overwrite it?"), confirmed)

        self.app.push_screen(SaveForm(*self._saved_as), chosen)

    def _save(self, name: str, description: str) -> None:
        try:
            prompt = self.current_prompt()
            model = self._ref("#model")
            judge = self._ref("#judge")
            harness = Harness(name=name, description=description, prompt=prompt,
                              cases=list(self.cases), accepted_model=model)
            entries = [self._entries[c.name] for c in self.cases
                       if c.name in self._entries and self._entries[c.name].case == c]
            fresh = [e for e in entries if model is not None and e.model == model
                     and e.judge == judge and e.prompt_hash == prompt.hash]
            self.db.save_harness(harness)
            self._saved_as = (name, description)
            self._mark_clean()
            if not fresh:
                self.notify(f"Saved {name!r}; no results for the current prompt, model and judge, "
                            "so no accepted run was stored", severity="warning")
                return
            run_id = save_accepted_run(self.db, name, prompt.hash, model, judge, fresh)
        except Exception as e:  # never crash the UI
            self.notify(f"Save failed: {type(e).__name__}: {e}", severity="error")
            return
        skipped = len(self.cases) - len(fresh)
        extra = f" ({skipped} case(s) without current results not included)" if skipped else ""
        self.notify(f"Saved harness {name!r} with accepted run #{run_id}{extra}")

    # -- dirty tracking --------------------------------------------------
    def _mark_clean(self) -> None:
        """Record the current prompt and cases as the clean (loaded/saved) state."""
        p = self._prompt_or_none()
        self._baseline = (p.hash if p is not None else None, list(self.cases))
        self._unsaved_results = False

    def is_dirty(self) -> bool:
        """True if loading a harness would lose work.

        Dirty means: results (or verdicts) were produced since the studio was last
        loaded/saved, or the cases or the prompt (system, template, temperature,
        max tokens) differ from that state. Starts clean (blank studio). The model
        and judge selections are not considered.
        """
        if self._unsaved_results:
            return True
        p = self._prompt_or_none()
        base_hash, base_cases = self._baseline
        return (p.hash if p is not None else None) != base_hash or self.cases != base_cases

    # -- load ------------------------------------------------------------
    async def load_harness(self, harness: Harness, model: ModelRef | None) -> bool:
        """Replace prompt, cases and model with `harness`'s; clears results and history.

        Does not switch tabs or focus (callers activate the studio tab first, then focus).
        Returns False (and notifies) if a studio run is in progress.
        """
        if self._busy():
            return False
        if self._edit_timer is not None:
            self._edit_timer.stop()
            self._edit_timer = None
        self._entries.clear()
        self.cases = []
        await self.query_one("#cases", ListView).clear()
        for case in harness.cases:
            await self.add_case(case)
        with self.prevent(TextArea.Changed, Input.Changed):
            self._restore(harness.prompt)
        self.history = PromptHistory()
        self.history.push(harness.prompt)
        listed = {f"{p.name}:{m}" for p in self.db.list_providers() if p.enabled
                  for m in self.db.list_models(p.name)}
        if model is not None and str(model) not in listed | set(self._manual_models):
            self._manual_models.append(str(model))
        self.refresh_models()
        with self.prevent(Select.Changed):
            if model is not None:
                self.query_one("#model", Select).value = str(model)
            else:
                self.query_one("#model", Select).clear()
            self.query_one("#judge", Select).clear()
        self._warned_pair = (None, model)
        self._saved_as = (harness.name, harness.description)
        self._log.clear()
        self.query_one("#output", RichLog).clear()
        self._write(Text(f"loaded harness {harness.name!r}", style="dim"))
        self._mark_clean()
        return True
