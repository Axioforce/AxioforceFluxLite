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

        self.lbl_title = QtWidgets.QLabel("Step off the plate")
        self.lbl_title.setAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter)
        self.lbl_title.setStyleSheet("font-size: 22px; font-weight: 700;")

        self.lbl_countdown = QtWidgets.QLabel("—")
        self.lbl_countdown.setAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter)
        try:
            self.lbl_countdown.setStyleSheet("font-size: 56px; font-weight: 800;")
        except Exception:
            pass
        self.lbl_force = QtWidgets.QLabel("Fz: — N")
        try:
            self.lbl_force.setStyleSheet("color: #AAA; font-size: 12px;")
        except Exception:
            pass

        bottom_row = QtWidgets.QHBoxLayout()
        bottom_row.addWidget(self.lbl_force)
        bottom_row.addStretch(1)

        root.addWidget(self.lbl_title)
        root.addStretch(1)
        root.addWidget(self.lbl_countdown)
        root.addStretch(1)
        root.addLayout(bottom_row)

    @QtCore.Slot(float)
    def set_force(self, fz_n: float) -> None:
        try:
            self.lbl_force.setText(f"Fz: {float(fz_n):.1f} N")
        except Exception:
            self.lbl_force.setText("Fz: — N")

    @QtCore.Slot(int)
    def set_countdown(self, seconds_remaining: int) -> None:
        try:
            self.lbl_countdown.setText(str(int(seconds_remaining)))
        except Exception:
            self.lbl_countdown.setText("—")

    def signal_ready(self) -> None:
        try:
            self.tare_ready.emit()
        except Exception:
            pass


