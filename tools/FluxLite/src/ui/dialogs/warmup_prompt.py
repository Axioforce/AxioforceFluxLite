from __future__ import annotations

from typing import Optional

from PySide6 import QtCore, QtWidgets


class WarmupPromptDialog(QtWidgets.QDialog):
    """Warmup dialog with a big countdown triggered by load."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None, duration_s: int = 20) -> None:
        super().__init__(parent)
        self.setWindowTitle("Warm Up the Plate")
        self.setModal(True)
        self.setMinimumWidth(420)

        self._duration_s = max(1, int(duration_s))

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        self.lbl_title = QtWidgets.QLabel("Jump on the plate")
        self.lbl_title.setAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter)
        self.lbl_title.setStyleSheet("font-size: 22px; font-weight: 700;")

        self.lbl_countdown = QtWidgets.QLabel("—")
        self.lbl_countdown.setAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter)
        try:
            self.lbl_countdown.setStyleSheet("font-size: 56px; font-weight: 800;")
        except Exception:
            pass

        root.addWidget(self.lbl_title)
        root.addStretch(1)
        root.addWidget(self.lbl_countdown)
        root.addStretch(1)

        self.set_waiting_for_trigger(True)

    def set_waiting_for_trigger(self, waiting: bool) -> None:
        # No extra UI beyond title+countdown; keep API for caller.
        if waiting:
            self.set_remaining(int(self._duration_s))

    @QtCore.Slot(int)
    def set_remaining(self, seconds_remaining: int) -> None:
        try:
            self.lbl_countdown.setText(str(int(seconds_remaining)))
        except Exception:
            self.lbl_countdown.setText("—")


