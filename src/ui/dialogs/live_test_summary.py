from __future__ import annotations

from typing import Optional

from PySide6 import QtCore, QtWidgets


class LiveTestSummaryDialog(QtWidgets.QDialog):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Live Testing - Summary")
        self.setModal(True)

        root = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()

        self.edit_tester = QtWidgets.QLineEdit()
        self.lbl_device = QtWidgets.QLabel("—")
        self.lbl_model = QtWidgets.QLabel("—")
        self.lbl_date = QtWidgets.QLabel("—")
        self.lbl_passfail = QtWidgets.QLabel("—")
        self.lbl_cells = QtWidgets.QLabel("—")
        self.lbl_grade = QtWidgets.QLabel("—")

        form.addRow("Tester:", self.edit_tester)
        form.addRow("Device ID:", self.lbl_device)
        form.addRow("Model ID:", self.lbl_model)
        form.addRow("Date:", self.lbl_date)
        form.addRow("Result:", self.lbl_passfail)
        form.addRow("Cells Passed:", self.lbl_cells)
        form.addRow("Grade:", self.lbl_grade)

        root.addLayout(form)

        self.btn_submit = QtWidgets.QPushButton("Submit")
        self.btn_cancel = QtWidgets.QPushButton("Cancel")
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addWidget(self.btn_submit)
        root.addLayout(btn_row)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_submit.clicked.connect(self.accept)

    def set_values(self, tester: str, device_id: str, model_id: str, date_text: str, pass_fail_text: str, pass_cells: int, total_cells: int, grade_text: str) -> None:
        self.edit_tester.setText(tester or "")
        self.lbl_device.setText(device_id or "—")
        self.lbl_model.setText(model_id or "—")
        self.lbl_date.setText(date_text or "—")
        self.lbl_passfail.setText(pass_fail_text or "—")
        try:
            self.lbl_cells.setText(f"{int(pass_cells)} / {int(total_cells)}")
        except Exception:
            self.lbl_cells.setText("—")
        self.lbl_grade.setText(grade_text or "—")

    def get_values(self) -> tuple[str, str]:
        return (self.edit_tester.text().strip(), self.lbl_passfail.text().strip())


