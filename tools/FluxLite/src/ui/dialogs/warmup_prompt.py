from __future__ import annotations

from typing import Optional

from PySide6 import QtCore, QtWidgets


class WarmupPromptDialog(QtWidgets.QDialog):
    """Simple countdown dialog used to warm up the plate before discrete temp testing."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None, duration_s: int = 20) -> None:
        super().__init__(parent)
        self.setWindowTitle("Warm Up the Plate")
        self.setModal(True)
        self.setMinimumWidth(420)

        self._remaining_s = max(1, int(duration_s))

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        self.lbl_title = QtWidgets.QLabel("Warm up the plate by jumping on it")
        self.lbl_title.setStyleSheet("font-size: 18px; font-weight: 600;")
        self.lbl_msg = QtWidgets.QLabel("Keep moving on the plate until the warmup timer finishes.")
        self.lbl_msg.setWordWrap(True)

        info_row = QtWidgets.QHBoxLayout()
        self.lbl_countdown = QtWidgets.QLabel("")
        info_row.addStretch(1)
        info_row.addWidget(self.lbl_countdown)

        self.btn_cancel = QtWidgets.QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_cancel)

        root.addWidget(self.lbl_title)
        root.addWidget(self.lbl_msg)
        root.addLayout(info_row)
        root.addStretch(1)
        root.addLayout(btn_row)

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._update_label()
        self._timer.start(1000)

    def _update_label(self) -> None:
        try:
            self.lbl_countdown.setText(f"Time remaining: {int(self._remaining_s)} s")
        except Exception:
            self.lbl_countdown.setText("Time remaining: — s")

    @QtCore.Slot()
    def _on_tick(self) -> None:
        try:
            self._remaining_s -= 1
            if self._remaining_s <= 0:
                self._timer.stop()
                self.accept()
                return
            self._update_label()
        except Exception:
            self._timer.stop()
            self.accept()


