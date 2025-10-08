from __future__ import annotations

from typing import Optional, Tuple

from PySide6 import QtCore, QtWidgets


class LiveTestSetupDialog(QtWidgets.QDialog):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Live Testing - Session Setup")
        self.setModal(True)

        root = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()

        self.edit_tester = QtWidgets.QLineEdit()
        self.lbl_device = QtWidgets.QLabel("—")
        self.lbl_model = QtWidgets.QLabel("—")
        self.spin_bw = QtWidgets.QDoubleSpinBox()
        self.spin_bw.setRange(0.0, 5000.0)
        self.spin_bw.setDecimals(1)
        self.spin_bw.setSuffix(" N")
        self.spin_bw.setSingleStep(1.0)

        form.addRow("Tester Name:", self.edit_tester)
        form.addRow("Device ID:", self.lbl_device)
        form.addRow("Model ID:", self.lbl_model)
        form.addRow("Body Weight:", self.spin_bw)
        # Tolerances are now read from config per model; no user inputs here

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        root.addLayout(form)
        root.addWidget(btns)

    def set_device_info(self, device_id: str, model_id: str) -> None:
        self.lbl_device.setText(device_id or "—")
        self.lbl_model.setText(model_id or "—")

    def set_defaults(self, tester: str, body_weight_n: float) -> None:
        self.edit_tester.setText(tester)
        try:
            self.spin_bw.setValue(float(body_weight_n))
        except Exception:
            pass

    def get_values(self) -> Tuple[str, float]:
        return (
            self.edit_tester.text().strip(),
            float(self.spin_bw.value()),
        )


