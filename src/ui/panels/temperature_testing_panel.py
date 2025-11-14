from __future__ import annotations

from typing import Optional

from PySide6 import QtCore, QtWidgets


class TemperatureTestingPanel(QtWidgets.QWidget):
    run_requested = QtCore.Signal(dict)
    browse_requested = QtCore.Signal()
    test_changed = QtCore.Signal(str)
    processed_selected = QtCore.Signal(object)
    view_mode_changed = QtCore.Signal(str)
    stage_changed = QtCore.Signal(str)
    test_changed = QtCore.Signal(str)
    processed_selected = QtCore.Signal(object)  # dict with slopes/paths
    view_mode_changed = QtCore.Signal(str)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)

        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(10)

        # Single settings pane (labels on the left of each control)
        settings_box = QtWidgets.QGroupBox("Temperature Testing")
        settings_layout = QtWidgets.QGridLayout(settings_box)

        # Folder selection
        self._folder_path: str = ""
        self.edit_folder = QtWidgets.QLineEdit()
        self.btn_browse = QtWidgets.QPushButton("Browse…")
        folder_row_widget = QtWidgets.QWidget()
        folder_row_layout = QtWidgets.QHBoxLayout(folder_row_widget)
        folder_row_layout.setContentsMargins(0, 0, 0, 0)
        folder_row_layout.setSpacing(6)
        folder_row_layout.addWidget(self.edit_folder, 1)
        folder_row_layout.addWidget(self.btn_browse, 0)
        settings_layout.addWidget(QtWidgets.QLabel("Device Folder:"), 0, 0)
        settings_layout.addWidget(folder_row_widget, 0, 1)

        # Device and model info
        self.lbl_device_id = QtWidgets.QLabel("—")
        self.lbl_model = QtWidgets.QLabel("—")
        self.lbl_bw = QtWidgets.QLabel("—")
        settings_layout.addWidget(QtWidgets.QLabel("Device ID:"), 1, 0)
        settings_layout.addWidget(self.lbl_device_id, 1, 1)
        settings_layout.addWidget(QtWidgets.QLabel("Latest Model:"), 2, 0)
        settings_layout.addWidget(self.lbl_model, 2, 1)
        settings_layout.addWidget(QtWidgets.QLabel("Body Weight (N):"), 3, 0)
        settings_layout.addWidget(self.lbl_bw, 3, 1)

        # Stage selector (moved to Display pane; placeholder init only)
        self.stage_combo = QtWidgets.QComboBox()
        self.stage_combo.addItems(["All"])

        # Test files list
        self.test_list = QtWidgets.QListWidget()
        settings_layout.addWidget(QtWidgets.QLabel("Tests in Device:"), 5, 0, QtCore.Qt.AlignTop)
        settings_layout.addWidget(self.test_list, 5, 1)

        # Slopes
        slopes_row = 6
        self.spin_x = QtWidgets.QDoubleSpinBox()
        self.spin_y = QtWidgets.QDoubleSpinBox()
        self.spin_z = QtWidgets.QDoubleSpinBox()
        for sp in (self.spin_x, self.spin_y, self.spin_z):
            sp.setRange(-1000.0, 1000.0)
            sp.setDecimals(3)
            sp.setSingleStep(0.1)
            sp.setValue(3.0)
        settings_layout.addWidget(QtWidgets.QLabel("Slope X:"), slopes_row + 0, 0)
        settings_layout.addWidget(self.spin_x, slopes_row + 0, 1)
        settings_layout.addWidget(QtWidgets.QLabel("Slope Y:"), slopes_row + 1, 0)
        settings_layout.addWidget(self.spin_y, slopes_row + 1, 1)
        settings_layout.addWidget(QtWidgets.QLabel("Slope Z:"), slopes_row + 2, 0)
        settings_layout.addWidget(self.spin_z, slopes_row + 2, 1)

        # View mode (moved to Display pane; placeholder init only)
        self.view_combo = QtWidgets.QComboBox()
        self.view_combo.addItems(["Heatmap", "Grid View"])

        # Run button
        self.btn_run = QtWidgets.QPushButton("Run (Temp On + Off)")

        # Left column (single settings pane)
        left_col = QtWidgets.QVBoxLayout()
        left_col.setSpacing(8)
        left_col.addWidget(settings_box, 1)
        left_col.addWidget(self.btn_run)
        left_wrap = QtWidgets.QWidget()
        left_wrap.setLayout(left_col)

        # Middle column: display (runs picker + view + stage)
        middle_box = QtWidgets.QGroupBox("Display")
        middle_layout = QtWidgets.QVBoxLayout(middle_box)
        middle_layout.setSpacing(6)
        middle_layout.addWidget(QtWidgets.QLabel("Processed Runs:"), 0)
        self.processed_list = QtWidgets.QListWidget()
        middle_layout.addWidget(self.processed_list, 1)
        controls_widget = QtWidgets.QWidget()
        controls_layout = QtWidgets.QGridLayout(controls_widget)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(6)
        controls_layout.addWidget(QtWidgets.QLabel("View:"), 0, 0)
        controls_layout.addWidget(self.view_combo, 0, 1)
        controls_layout.addWidget(QtWidgets.QLabel("Stage:"), 1, 0)
        controls_layout.addWidget(self.stage_combo, 1, 1)
        middle_layout.addWidget(controls_widget, 0)

        # Right column: metrics compare
        right_box = QtWidgets.QGroupBox("Metrics (Baseline vs Selected)")
        right_layout = QtWidgets.QGridLayout(right_box)
        self.lbl_base_cnt = QtWidgets.QLabel("—")
        self.lbl_base_mean = QtWidgets.QLabel("—")
        self.lbl_base_med = QtWidgets.QLabel("—")
        self.lbl_base_max = QtWidgets.QLabel("—")
        self.lbl_sel_cnt = QtWidgets.QLabel("—")
        self.lbl_sel_mean = QtWidgets.QLabel("—")
        self.lbl_sel_med = QtWidgets.QLabel("—")
        self.lbl_sel_max = QtWidgets.QLabel("—")
        right_layout.addWidget(QtWidgets.QLabel("Baseline Count:"), 0, 0)
        right_layout.addWidget(self.lbl_base_cnt, 0, 1)
        right_layout.addWidget(QtWidgets.QLabel("Baseline Mean%:"), 1, 0)
        right_layout.addWidget(self.lbl_base_mean, 1, 1)
        right_layout.addWidget(QtWidgets.QLabel("Baseline Median%:"), 2, 0)
        right_layout.addWidget(self.lbl_base_med, 2, 1)
        right_layout.addWidget(QtWidgets.QLabel("Baseline Max%:"), 3, 0)
        right_layout.addWidget(self.lbl_base_max, 3, 1)
        right_layout.addWidget(QtWidgets.QLabel("Selected Count:"), 4, 0)
        right_layout.addWidget(self.lbl_sel_cnt, 4, 1)
        right_layout.addWidget(QtWidgets.QLabel("Selected Mean%:"), 5, 0)
        right_layout.addWidget(self.lbl_sel_mean, 5, 1)
        right_layout.addWidget(QtWidgets.QLabel("Selected Median%:"), 6, 0)
        right_layout.addWidget(self.lbl_sel_med, 6, 1)
        right_layout.addWidget(QtWidgets.QLabel("Selected Max%:"), 7, 0)
        right_layout.addWidget(self.lbl_sel_max, 7, 1)
        right_layout.setRowStretch(8, 1)

        root.addWidget(left_wrap, 1)
        root.addWidget(middle_box, 1)
        root.addWidget(right_box, 2)
        try:
            root.setStretch(0, 1)  # left ~ 1/4
            root.setStretch(1, 1)  # middle ~ 1/4
            root.setStretch(2, 2)  # right ~ 1/2
        except Exception:
            pass

        self.btn_browse.clicked.connect(lambda: self.browse_requested.emit())
        self.btn_run.clicked.connect(self._emit_run)
        self.view_combo.currentTextChanged.connect(lambda s: self.view_mode_changed.emit(str(s)))
        self.test_list.currentItemChanged.connect(self._emit_test_changed)
        self.processed_list.currentItemChanged.connect(self._emit_processed_changed)
        self.stage_combo.currentTextChanged.connect(lambda s: self.stage_changed.emit(str(s)))
        self.view_combo.currentTextChanged.connect(lambda s: self.view_mode_changed.emit(str(s)))
        self.test_list.currentItemChanged.connect(self._emit_test_changed)
        self.processed_list.currentItemChanged.connect(self._emit_processed_changed)

    def set_folder(self, path: str) -> None:
        import os
        self._folder_path = path
        try:
            self.edit_folder.setText(os.path.basename(path.rstrip("\\/")) or path)
        except Exception:
            self.edit_folder.setText(path)

    def set_device_id(self, device_id: str) -> None:
        self.lbl_device_id.setText(device_id or "—")

    def set_model_label(self, model_text: str) -> None:
        self.lbl_model.setText(model_text or "—")

    def set_body_weight_n(self, bw_n: Optional[float]) -> None:
        try:
            if bw_n is None:
                self.lbl_bw.setText("—")
            else:
                self.lbl_bw.setText(f"{float(bw_n):.1f}")
        except Exception:
            self.lbl_bw.setText("—")

    def set_tests(self, files: list[str]) -> None:
        self.test_list.clear()
        import os
        for f in files:
            label = os.path.basename(f.rstrip("\\/")) if f else f
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, f)  # store full path
            self.test_list.addItem(item)
        if self.test_list.count() > 0:
            self.test_list.setCurrentRow(0)

    def set_stages(self, stages: list[str]) -> None:
        stages = stages or ["All"]
        if "All" not in stages:
            stages = ["All"] + [s for s in stages if s != "All"]
        self.stage_combo.blockSignals(True)
        self.stage_combo.clear()
        self.stage_combo.addItems(stages)
        self.stage_combo.blockSignals(False)

    def set_processed_runs(self, entries: list[dict]) -> None:
        self.processed_list.clear()
        for e in entries:
            label = e.get("label") or e.get("path") or ""
            it = QtWidgets.QListWidgetItem(str(label))
            it.setData(QtCore.Qt.UserRole, dict(e))
            self.processed_list.addItem(it)
        if self.processed_list.count() > 0:
            self.processed_list.setCurrentRow(0)

    def selected_test(self) -> str:
        it = self.test_list.currentItem()
        return str(it.data(QtCore.Qt.UserRole)) if it is not None else ""

    def slopes(self) -> tuple[float, float, float]:
        return float(self.spin_x.value()), float(self.spin_y.value()), float(self.spin_z.value())

    def _emit_run(self) -> None:
        payload = {
            "folder": (self._folder_path or self.edit_folder.text().strip()),
            "device_id": self.lbl_device_id.text().strip(),
            "csv_path": self.selected_test(),
            "slopes": {"x": float(self.spin_x.value()), "y": float(self.spin_y.value()), "z": float(self.spin_z.value())},
        }
        self.run_requested.emit(payload)

    def _emit_test_changed(self) -> None:
        it = self.test_list.currentItem()
        path = str(it.data(QtCore.Qt.UserRole)) if it is not None else ""
        self.test_changed.emit(path)

    def _emit_processed_changed(self) -> None:
        it = self.processed_list.currentItem()
        data = dict(it.data(QtCore.Qt.UserRole)) if it is not None else {}
        self.processed_selected.emit(data)
