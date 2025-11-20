from __future__ import annotations

from typing import Optional, Tuple
import os

from PySide6 import QtCore, QtWidgets


class LiveTestSetupDialog(QtWidgets.QDialog):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None, is_temp_test: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("Live Testing - Session Setup")
        self.setModal(True)

        self._device_id: str = ""
        # Session mode (Normal vs Temperature Test) is now provided by caller.
        self._is_temp_test: bool = bool(is_temp_test)

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

        # New options
        # Session type is now selected from the main Live Testing panel; no toggle here.
        self.chk_capture = QtWidgets.QCheckBox("Capture (CSV/logs)")

        # Save location row (hidden until Capture is enabled)
        self.edit_save_dir = QtWidgets.QLineEdit()
        self.btn_browse = QtWidgets.QPushButton("Browse…")
        save_row_widget = QtWidgets.QWidget()
        save_row_layout = QtWidgets.QHBoxLayout(save_row_widget)
        save_row_layout.setContentsMargins(0, 0, 0, 0)
        save_row_layout.setSpacing(6)
        save_row_layout.addWidget(self.edit_save_dir, 1)
        save_row_layout.addWidget(self.btn_browse, 0)

        form.addRow("Tester Name:", self.edit_tester)
        form.addRow("Device ID:", self.lbl_device)
        form.addRow("Model ID:", self.lbl_model)
        form.addRow("Body Weight:", self.spin_bw)
        form.addRow(self.chk_capture)
        form.addRow("CSV Save Location:", save_row_widget)
        # Removed 'Bypass Models' control per backend ownership

        # Initially hide capture-dependent controls
        self._set_capture_controls_visible(False)

        # Wire interactions
        self.chk_capture.toggled.connect(self._on_capture_toggled)
        self.btn_browse.clicked.connect(self._choose_directory)

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        root.addLayout(form)
        root.addWidget(btns)

    def _project_root(self) -> str:
        # Project root: three levels up from this file (src/ui/dialogs -> src -> root)
        try:
            return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        except Exception:
            return os.getcwd()

    def _app_default_dir(self, is_temp: bool) -> str:
        # Root-level folders: live_test_logs/ and temp_testing/
        base = self._project_root()
        folder = "temp_testing" if is_temp else "live_test_logs"
        path = os.path.join(base, folder)
        if self._device_id:
            path = os.path.join(path, self._device_id)
        return path

    def _ensure_default_save_dir(self) -> None:
        if not self.edit_save_dir.text().strip():
            # Default based on session mode (Normal vs Temperature Test)
            self.edit_save_dir.setText(self._app_default_dir(self._is_temp_test))

    def _set_capture_controls_visible(self, visible: bool) -> None:
        self.edit_save_dir.setVisible(visible)
        self.btn_browse.setVisible(visible)

    def _on_capture_toggled(self, enabled: bool) -> None:
        self._set_capture_controls_visible(bool(enabled))
        if enabled:
            self._ensure_default_save_dir()

    def _choose_directory(self) -> None:
        options = QtWidgets.QFileDialog.Options()
        start_dir = self.edit_save_dir.text().strip() or self._app_default_dir(self._is_temp_test)
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose CSV Save Folder", start_dir, options=options)
        if directory:
            self.edit_save_dir.setText(directory)

    def set_device_info(self, device_id: str, model_id: str) -> None:
        self.lbl_device.setText(device_id or "—")
        self.lbl_model.setText(model_id or "—")
        self._device_id = (device_id or "").strip()

    def set_defaults(self, tester: str, body_weight_n: float) -> None:
        self.edit_tester.setText(tester)
        try:
            self.spin_bw.setValue(float(body_weight_n))
        except Exception:
            pass
        # Defaults: off
        self.chk_capture.setChecked(False)
        self.edit_save_dir.clear()

    def get_values(self) -> Tuple[str, float, bool, bool, str]:
        return (
            self.edit_tester.text().strip(),
            float(self.spin_bw.value()),
            bool(self._is_temp_test),
            bool(self.chk_capture.isChecked()),
            self.edit_save_dir.text().strip(),
        )


