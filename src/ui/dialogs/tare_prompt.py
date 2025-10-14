from __future__ import annotations

from typing import Optional

from PySide6 import QtCore, QtWidgets


class TarePromptDialog(QtWidgets.QDialog):
    """Modal dialog that instructs the user to step off the plate and shows a live countdown.

    The dialog exposes simple slots to update the current force reading and remaining seconds,
    and emits a signal when the 15-second countdown has successfully completed under threshold.
    """

    tare_ready = QtCore.Signal()

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Step Off to Tare")
        self.setModal(True)
        self.setMinimumWidth(420)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        self.lbl_title = QtWidgets.QLabel("Please step off the plate")
        self.lbl_title.setStyleSheet("font-size: 18px; font-weight: 600;")
        self.lbl_msg = QtWidgets.QLabel("When the force drops below 30 N, a 15 second timer will start.")
        self.lbl_msg.setWordWrap(True)

        info_row = QtWidgets.QHBoxLayout()
        self.lbl_force = QtWidgets.QLabel("Force: — N")
        self.lbl_countdown = QtWidgets.QLabel("Countdown: — s")
        info_row.addWidget(self.lbl_force)
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

    @QtCore.Slot(float)
    def set_force(self, fz_n: float) -> None:
        try:
            self.lbl_force.setText(f"Force: {float(fz_n):.1f} N")
        except Exception:
            self.lbl_force.setText("Force: — N")

    @QtCore.Slot(int)
    def set_countdown(self, seconds_remaining: int) -> None:
        try:
            self.lbl_countdown.setText(f"Countdown: {int(seconds_remaining)} s")
        except Exception:
            self.lbl_countdown.setText("Countdown: — s")

    def signal_ready(self) -> None:
        try:
            self.tare_ready.emit()
        except Exception:
            pass


