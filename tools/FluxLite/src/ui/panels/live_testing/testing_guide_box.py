from __future__ import annotations

from typing import Optional

from PySide6 import QtWidgets


class TestingGuideBox(QtWidgets.QGroupBox):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__("Testing Guide", parent)
        layout = QtWidgets.QVBoxLayout(self)
        self.guide_label = QtWidgets.QLabel("Use Start Session to begin. Follow prompts here.")
        self.guide_label.setWordWrap(True)
        layout.addWidget(self.guide_label)
        layout.addStretch(1)

    def set_stage_progress(self, stage_text: str, completed_cells: int, total_cells: int) -> None:
        try:
            self.guide_label.setText(
                f"{stage_text}\n\n"
                "Instructions:\n"
                "- Place the specified load in any cell.\n"
                "- Keep COP inside the cell until stable (≈2s, Fz steady).\n"
                "- When captured, the cell will colorize. Move to next cell.\n"
                "- After all cells, follow prompts for the next stage/location."
            )
        except Exception:
            pass


