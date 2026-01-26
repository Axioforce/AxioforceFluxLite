from __future__ import annotations

from typing import Optional, Tuple

from PySide6 import QtCore, QtWidgets


class ModelPackagerDialog(QtWidgets.QDialog):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Package Model")

        root = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()

        # Force model file
        self.edit_force = QtWidgets.QLineEdit()
        btn_force = QtWidgets.QPushButton("Browse…")
        row_force = QtWidgets.QHBoxLayout()
        row_force.addWidget(self.edit_force)
        row_force.addWidget(btn_force)
        form.addRow("Force Model File:", self._wrap(row_force))

        # Moments model file
        self.edit_moments = QtWidgets.QLineEdit()
        btn_moments = QtWidgets.QPushButton("Browse…")
        row_moments = QtWidgets.QHBoxLayout()
        row_moments.addWidget(self.edit_moments)
        row_moments.addWidget(btn_moments)
        form.addRow("Moments Model File:", self._wrap(row_moments))

        # Output dir
        self.edit_output = QtWidgets.QLineEdit()
        btn_output = QtWidgets.QPushButton("Browse…")
        row_output = QtWidgets.QHBoxLayout()
        row_output.addWidget(self.edit_output)
        row_output.addWidget(btn_output)
        form.addRow("Output Dir:", self._wrap(row_output))

        root.addLayout(form)

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        root.addWidget(btns)

        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        btn_force.clicked.connect(lambda: self._pick_file(self.edit_force))
        btn_moments.clicked.connect(lambda: self._pick_file(self.edit_moments))
        btn_output.clicked.connect(lambda: self._pick_dir(self.edit_output))

    def _wrap(self, layout: QtWidgets.QHBoxLayout) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        w.setLayout(layout)
        return w

    def _pick_dir(self, target_edit: QtWidgets.QLineEdit) -> None:
        try:
            dir_path = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Directory")
        except Exception:
            dir_path = ""
        if dir_path:
            target_edit.setText(dir_path)

    def _pick_file(self, target_edit: QtWidgets.QLineEdit) -> None:
        try:
            file_path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select Model File", filter="All Files (*)")
        except Exception:
            file_path = ""
        if file_path:
            target_edit.setText(file_path)

    def get_values(self) -> Tuple[str, str, str]:
        return (
            self.edit_force.text().strip(),
            self.edit_moments.text().strip(),
            self.edit_output.text().strip(),
        )


