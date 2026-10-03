from __future__ import annotations

from textual.widgets import Static


class StudioPane(Static):
    """Placeholder; replaced by a later task."""

    def __init__(self, **kwargs) -> None:
        super().__init__("Studio (coming soon)", **kwargs)
