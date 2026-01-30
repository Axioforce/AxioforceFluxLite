from __future__ import annotations

from typing import Optional

from PySide6 import QtCore, QtWidgets


class StageSwitchPromptDialog(QtWidgets.QDialog):
    """
    Modal dialog that prompts the user to switch to a different stage (e.g., Bodyweight or 45 lb).

    The dialog waits for the force to drop below a threshold before automatically
    switching stages and closing.
    """

    switch_ready = QtCore.Signal()

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None, target_stage: str = "Bodyweight") -> None:
        super().__init__(parent)
        self.setWindowTitle("Switch Stage")
        self.setModal(True)
        self.setMinimumWidth(350)

        self._target_stage = str(target_stage or "Bodyweight")

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)

        self.lbl_title = QtWidgets.QLabel(f"Switch to {self._target_stage}")
        self.lbl_title.setAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter)
        self.lbl_title.setStyleSheet("font-size: 22px; font-weight: 700;")

        self.lbl_instruction = QtWidgets.QLabel("Remove load from plate to continue")
        self.lbl_instruction.setAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter)
        try:
            self.lbl_instruction.setStyleSheet("font-size: 14px; color: #BDBDBD;")
        except Exception:
            pass

        root.addStretch(1)
        root.addWidget(self.lbl_title)
        root.addWidget(self.lbl_instruction)
        root.addStretch(1)

    def set_target_stage(self, stage_name: str) -> None:
        """Update the target stage name displayed in the dialog."""
        self._target_stage = str(stage_name or "Bodyweight")
        try:
            self.lbl_title.setText(f"Switch to {self._target_stage}")
        except Exception:
            pass

    @QtCore.Slot(float)
    def set_force(self, fz_n: float) -> None:
        """Update the current force reading (kept for API compatibility, but not displayed)."""
        pass

    def signal_ready(self) -> None:
        """Emit the switch_ready signal when force drops below threshold."""
        try:
            self.switch_ready.emit()
        except Exception:
            pass
