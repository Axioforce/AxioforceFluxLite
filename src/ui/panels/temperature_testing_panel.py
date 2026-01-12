from __future__ import annotations

import datetime
import json
import os
from typing import Optional

from PySide6 import QtCore, QtWidgets

from ..widgets.temp_testing_metrics_widget import TempTestingMetricsWidget


class ProcessedRunItemWidget(QtWidgets.QWidget):
    delete_requested = QtCore.Signal(str)

    def __init__(self, text: str, file_path: str, item: QtWidgets.QListWidgetItem, list_widget: QtWidgets.QListWidget, parent=None):
        super().__init__(parent)
        self.file_path = file_path
        self.item = item
        self.list_widget = list_widget

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(8)
        
        self.label = QtWidgets.QLabel(text)
        self.label.setStyleSheet("background: transparent;")
        layout.addWidget(self.label, 1)
        
        self.btn_delete = QtWidgets.QPushButton("×")
        self.btn_delete.setFixedSize(20, 20)
        self.btn_delete.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_delete.setStyleSheet("""
            QPushButton {
                border: none;
                color: #888;
                font-weight: bold;
                font-size: 16px;
                background: transparent;
                margin: 0px;
                padding: 0px;
            }
            QPushButton:hover {
                color: #ff4444;
                background: rgba(255, 0, 0, 0.1);
                border-radius: 10px;
            }
        """)
        self.btn_delete.setToolTip("Delete this processed run")
        self.btn_delete.clicked.connect(self._on_delete)
        layout.addWidget(self.btn_delete, 0)

    def _on_delete(self):
        self.delete_requested.emit(self.file_path)

    def mousePressEvent(self, event):
        self.list_widget.setCurrentItem(self.item)
        super().mousePressEvent(event)


class TemperatureTestingPanel(QtWidgets.QWidget):
    run_requested = QtCore.Signal(dict)
    device_selected = QtCore.Signal(str)
    refresh_requested = QtCore.Signal()
    test_changed = QtCore.Signal(str)
    processed_selected = QtCore.Signal(object)  # dict with slopes/paths
    stage_changed = QtCore.Signal(str)
    plot_stages_requested = QtCore.Signal()  # Request matplotlib stage visualization
    grading_mode_changed = QtCore.Signal(str)  # "Absolute" | "Bias Controlled"

    def __init__(self, controller: object = None, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.controller = controller

        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(10)

        # Single settings pane (labels on the left of each control)
        settings_box = QtWidgets.QGroupBox("Temperature Testing")
        settings_layout = QtWidgets.QGridLayout(settings_box)

        # Device selection
        self.device_combo = QtWidgets.QComboBox()
        self.btn_refresh = QtWidgets.QPushButton("Refresh")
        device_row_widget = QtWidgets.QWidget()
        device_row_layout = QtWidgets.QHBoxLayout(device_row_widget)
        device_row_layout.setContentsMargins(0, 0, 0, 0)
        device_row_layout.setSpacing(6)
        device_row_layout.addWidget(self.device_combo, 1)
        device_row_layout.addWidget(self.btn_refresh, 0)
        settings_layout.addWidget(QtWidgets.QLabel("Device:"), 0, 0)
        settings_layout.addWidget(device_row_widget, 0, 1)

        # Device and model info
        self.lbl_device_id = QtWidgets.QLabel("—")
        self.lbl_model = QtWidgets.QLabel("—")
        self.lbl_bw = QtWidgets.QLabel("—")
        # Removed from layout per request
        # settings_layout.addWidget(QtWidgets.QLabel("Device ID:"), 1, 0)
        # settings_layout.addWidget(self.lbl_device_id, 1, 1)
        # settings_layout.addWidget(QtWidgets.QLabel("Latest Model:"), 2, 0)
        # settings_layout.addWidget(self.lbl_model, 2, 1)
        # settings_layout.addWidget(QtWidgets.QLabel("Body Weight (N):"), 3, 0)
        # settings_layout.addWidget(self.lbl_bw, 3, 1)

        # Stage selector (moved to Display pane; placeholder init only)
        self.stage_combo = QtWidgets.QComboBox()
        self.stage_combo.addItems(["All"])

        # Test files list
        self.test_list = QtWidgets.QListWidget()
        settings_layout.addWidget(QtWidgets.QLabel("Tests in Device:"), 1, 0, QtCore.Qt.AlignTop)
        settings_layout.addWidget(self.test_list, 1, 1)
        self.test_list.installEventFilter(self)
        self.test_list.viewport().installEventFilter(self)

        # Scalar temperature coefficients
        slopes_row = 2

        self.spin_x = QtWidgets.QDoubleSpinBox()
        self.spin_y = QtWidgets.QDoubleSpinBox()
        self.spin_z = QtWidgets.QDoubleSpinBox()
        for sp in (self.spin_x, self.spin_y, self.spin_z):
            sp.setRange(-1000.0, 1000.0)
            # Scalar coefficients are small; make entry practical.
            sp.setDecimals(6)
            sp.setSingleStep(0.0001)
            sp.setValue(0.002)
        
        self.lbl_slope_x = QtWidgets.QLabel("Coef X:")
        settings_layout.addWidget(self.lbl_slope_x, slopes_row + 0, 0)
        settings_layout.addWidget(self.spin_x, slopes_row + 0, 1)
        
        self.lbl_slope_y = QtWidgets.QLabel("Coef Y:")
        settings_layout.addWidget(self.lbl_slope_y, slopes_row + 1, 0)
        settings_layout.addWidget(self.spin_y, slopes_row + 1, 1)
        
        self.lbl_slope_z = QtWidgets.QLabel("Coef Z:")
        settings_layout.addWidget(self.lbl_slope_z, slopes_row + 2, 0)
        settings_layout.addWidget(self.spin_z, slopes_row + 2, 1)

        # Run button
        self.btn_run = QtWidgets.QPushButton("Process")
        self.btn_run_plate_type = QtWidgets.QPushButton("Run current coefs across plate type")
        self.btn_run_plate_type.setToolTip(
            "Runs the current coefficients across all devices of this plate type for all tests with meta, generating missing outputs."
        )

        # Left column (single settings pane)
        left_col = QtWidgets.QVBoxLayout()
        left_col.setSpacing(8)
        left_col.addWidget(settings_box, 1)
        left_col.addWidget(self.btn_run)
        left_col.addWidget(self.btn_run_plate_type)
        left_wrap = QtWidgets.QWidget()
        left_wrap.setLayout(left_col)

        # Middle column: display (runs picker + view + stage)
        middle_box = QtWidgets.QGroupBox("Display")
        middle_layout = QtWidgets.QVBoxLayout(middle_box)
        middle_layout.setSpacing(6)
        processed_label_row = QtWidgets.QHBoxLayout()
        processed_label = QtWidgets.QLabel("Processed Runs:")
        self.analysis_status_label = QtWidgets.QLabel()
        self.analysis_status_label.setVisible(False)
        processed_label_row.addWidget(processed_label)
        processed_label_row.addWidget(self.analysis_status_label, 0, QtCore.Qt.AlignRight)
        processed_label_row.addStretch(1)
        middle_layout.addLayout(processed_label_row)
        self.processed_list = QtWidgets.QListWidget()
        middle_layout.addWidget(self.processed_list, 1)
        controls_widget = QtWidgets.QWidget()
        controls_layout = QtWidgets.QHBoxLayout(controls_widget)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(6)
        controls_layout.addWidget(QtWidgets.QLabel("Stage:"))
        controls_layout.addWidget(self.stage_combo)
        self.btn_plot_stages = QtWidgets.QPushButton("Plot")
        self.btn_plot_stages.setFixedWidth(60)
        self.btn_plot_stages.setToolTip("Show matplotlib visualization of stage detection windows")
        controls_layout.addWidget(self.btn_plot_stages)

        controls_layout.addWidget(QtWidgets.QLabel("Grading:"))
        self.grading_combo = QtWidgets.QComboBox()
        self.grading_combo.addItems(["Absolute", "Bias Controlled"])
        self.grading_combo.setToolTip(
            "Absolute: grade vs truth targets. Bias Controlled: grade vs room-temp baseline behavior."
        )
        controls_layout.addWidget(self.grading_combo)
        controls_layout.addStretch(1)
        
        middle_layout.addWidget(controls_widget, 0)

        # Right column: metrics (reworked)
        right_box = QtWidgets.QGroupBox("Metrics")
        right_layout = QtWidgets.QVBoxLayout(right_box)
        self.metrics_widget = TempTestingMetricsWidget()
        right_layout.addWidget(self.metrics_widget, 1)

        root.addWidget(left_wrap, 1)
        root.addWidget(middle_box, 1)
        root.addWidget(right_box, 2)
        try:
            root.setStretch(0, 1)  # left ~ 1/4
            root.setStretch(1, 1)  # middle ~ 1/4
            root.setStretch(2, 2)  # right ~ 1/2
        except Exception:
            pass

        self.device_combo.currentTextChanged.connect(self._on_device_changed)
        self.btn_refresh.clicked.connect(self._on_refresh_clicked)
        self.btn_run.clicked.connect(self._on_run_clicked)
        self.btn_run_plate_type.clicked.connect(self._on_run_plate_type_clicked)
        self.test_list.currentItemChanged.connect(self._emit_test_changed)
        self.processed_list.currentItemChanged.connect(self._emit_processed_changed)
        self.stage_combo.currentTextChanged.connect(lambda s: self.stage_changed.emit(str(s)))
        self.btn_plot_stages.clicked.connect(lambda: self.plot_stages_requested.emit())
        self.grading_combo.currentTextChanged.connect(lambda s: self.grading_mode_changed.emit(str(s)))

        self._processing_timer = QtCore.QTimer(self)
        self._processing_timer.setInterval(120)
        self._processing_timer.timeout.connect(self._on_spinner_tick)
        self._spinner_frames = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]
        self._spinner_index = 0
        self._processing_text = "Processing…"
        self._processing_active = False
        self._analysis_timer = QtCore.QTimer(self)
        self._analysis_timer.setInterval(140)
        self._analysis_timer.timeout.connect(self._on_analysis_spinner_tick)
        self._analysis_frames = ["◐", "◓", "◑", "◒"]
        self._analysis_index = 0
        self._analysis_active = False
        
        if self.controller:
            self.controller.tests_listed.connect(self.set_tests)
            self.controller.devices_listed.connect(self.set_devices)
            self.controller.processed_runs_loaded.connect(self.set_processed_runs)
            self.controller.stages_loaded.connect(self.set_stages)
            self.controller.test_meta_loaded.connect(self._on_test_meta_loaded)
            self.controller.processing_status.connect(self._on_processing_status)
            self.controller.analysis_status.connect(self._on_analysis_status)
            try:
                self.controller.bias_status.connect(self._on_bias_status)
            except Exception:
                pass
            self.test_changed.connect(self.controller.load_test_details)
            # Big picture: reset plate-type top3 (clear rollup cache)
            try:
                self.metrics_widget.btn_reset_top3.clicked.connect(self._on_reset_top3_clicked)
            except Exception:
                pass
            try:
                self.controller.rollup_ready.connect(self._on_rollup_ready)
            except Exception:
                pass
            
            # IMPORTANT: Do NOT auto-select and auto-run analysis on app startup.
            # We still allow explicit user-driven refresh via the Refresh button.
        self._bias_available = False
        self.set_bias_mode_available(False, "")

    def grading_mode(self) -> str:
        text = str(self.grading_combo.currentText() or "Absolute").strip().lower()
        return "bias" if text.startswith("bias") else "absolute"

    def set_bias_mode_available(self, available: bool, message: str = "") -> None:
        """
        Enable/disable the 'Bias Controlled' grading option.
        When disabling, forces selection back to Absolute.
        """
        self._bias_available = bool(available)
        try:
            model = self.grading_combo.model()
            item = model.item(1) if model is not None else None  # index 1 = Bias Controlled
            if item is not None:
                item.setEnabled(bool(available))
        except Exception:
            pass

        if not available:
            try:
                self.grading_combo.blockSignals(True)
                self.grading_combo.setCurrentIndex(0)
            finally:
                try:
                    self.grading_combo.blockSignals(False)
                except Exception:
                    pass

        if message:
            try:
                QtWidgets.QMessageBox.warning(self, "Bias-Controlled Grading", str(message))
            except Exception:
                pass

    def _on_bias_status(self, payload: dict) -> None:
        payload = payload or {}
        available = bool(payload.get("available"))
        message = str(payload.get("message") or "")
        self.set_bias_mode_available(available, message)

    def _on_run_plate_type_clicked(self) -> None:
        """
        Run the current coefficient settings across all devices/tests for this plate type.
        """
        if not self.controller:
            return
        try:
            x, y, z = self.slopes()
            coefs = {"x": float(x), "y": float(y), "z": float(z)}
            self.metrics_widget.set_big_picture_status("Running batch rollup…")
            self.controller.run_coefs_across_plate_type(coefs, "scalar")
        except Exception as exc:
            try:
                QtWidgets.QMessageBox.warning(self, "Batch Rollup", str(exc))
            except Exception:
                pass

    def _on_reset_top3_clicked(self) -> None:
        """
        Clear the stored plate-type rollup that feeds the Top-3 list.
        This can be expensive to regenerate, so we always confirm.
        """
        if not self.controller:
            return

        pt = ""
        try:
            pt = str(self.controller.current_plate_type() or "").strip()
        except Exception:
            pt = ""

        title = "Confirm Reset"
        msg = (
            "This will clear the stored plate-type rollup used to compute the Top 3 coef combos.\n\n"
            "Regenerating it can take a lot of compute.\n\n"
            f"Plate type: {pt or '—'}"
        )
        reply = QtWidgets.QMessageBox.question(
            self,
            title,
            msg,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return

        try:
            self.metrics_widget.set_big_picture_status("Clearing rollup…")
        except Exception:
            pass
        self.controller.reset_rollup_for_current_plate_type(backup=True)

    def _on_rollup_ready(self, payload: dict) -> None:
        payload = payload or {}
        ok = bool(payload.get("ok"))
        msg = str(payload.get("message") or "")
        errs = list(payload.get("errors") or [])
        if ok:
            self.metrics_widget.set_big_picture_status(msg or "Batch rollup complete")
            try:
                top3 = self.controller.top3_for_current_plate_type() if self.controller else []
                self.metrics_widget.set_top3(list(top3 or []))
            except Exception:
                pass
        else:
            details = "\n".join([msg] + [f"- {e}" for e in errs if e])
            self.metrics_widget.set_big_picture_status("Batch rollup failed")
            try:
                QtWidgets.QMessageBox.warning(self, "Batch Rollup", details or "Batch rollup failed.")
            except Exception:
                pass

    def set_devices(self, devices: list[str]) -> None:
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        self.device_combo.addItems(devices)
        # Avoid implicit selection which would cascade into test selection and analysis.
        try:
            self.device_combo.setCurrentIndex(-1)
        except Exception:
            pass
        self.device_combo.blockSignals(False)
        # Clear dependent UI when device list changes.
        try:
            self.test_list.clear()
            self.processed_list.clear()
            self._clear_metrics()
        except Exception:
            pass
        try:
            self.test_changed.emit("")
        except Exception:
            pass

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
        items = []
        available = self._available_label_width()
        for f in files or []:
            if not f:
                continue
            label = self._build_test_label(f, available)
            items.append((label, f))
        if not items and files:
            # Fallback: show raw names if formatting failed
            items = [(os.path.basename(f.rstrip("\\/")), f) for f in files if f]
        self.set_tests_with_labels(items)

    def set_tests_with_labels(self, items: list[tuple[str, str]]) -> None:
        self.test_list.clear()
        available = self._available_label_width()
        for label, path in items:
            display = self._build_test_label(path, available) if path else label
            item = QtWidgets.QListWidgetItem(display)
            item.setData(QtCore.Qt.UserRole, path)  # store full path
            self.test_list.addItem(item)
        # Do not auto-select the first test; user must explicitly pick one.
        if self.test_list.count() == 0:
            self.test_changed.emit("")
        self._refresh_test_labels()

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
        for e in entries or []:
            if e.get("is_baseline"):
                continue
            label = e.get("label") or e.get("path") or ""
            mode = str(e.get("mode") or "").capitalize()
            if mode == "Legacy":
                # Scalar is the default/normal mode; only tag legacy runs so they stand out.
                label = f"{label} [{mode}]"
            elif not mode:
                # If no meta exists, assume legacy (backwards compatibility) and avoid tagging.
                pass
                
            path = str(e.get("path") or "")

            it = QtWidgets.QListWidgetItem()
            it.setData(QtCore.Qt.UserRole, dict(e))
            self.processed_list.addItem(it)
            
            widget = ProcessedRunItemWidget(str(label), path, it, self.processed_list)
            widget.delete_requested.connect(self._on_delete_processed_requested)
            
            it.setSizeHint(widget.sizeHint())
            self.processed_list.setItemWidget(it, widget)

        # Do not auto-select a processed run. Selecting one triggers analysis which
        # should only happen on explicit user action.
        if self.processed_list.count() == 0:
            try:
                self._clear_metrics()
            except Exception:
                pass

    def selected_test(self) -> str:
        it = self.test_list.currentItem()
        return str(it.data(QtCore.Qt.UserRole)) if it is not None else ""

    def slopes(self) -> tuple[float, float, float]:
        return float(self.spin_x.value()), float(self.spin_y.value()), float(self.spin_z.value())

    def current_stage(self) -> str:
        """Return current stage selection: 'All', 'db', or 'bw'."""
        text = str(self.stage_combo.currentText() or "All").strip()
        if text.lower().startswith("45") or text.lower() == "db":
            return "db"
        elif text.lower().startswith("body") or text.lower() == "bw":
            return "bw"
        return "All"

    def set_analysis_metrics(
        self,
        payload: dict,
        *,
        device_type: str = "06",
        body_weight_n: float = 0.0,
        bias_cache: Optional[dict] = None,
        bias_map_all=None,
        grading_mode: str = "absolute",
    ) -> None:
        """
        Update the metrics widget from analysis results.
        """
        try:
            self.metrics_widget.set_bias_cache(bias_cache)
            self.metrics_widget.set_run_metrics(
                payload,
                device_type=str(device_type or "06"),
                body_weight_n=float(body_weight_n or 0.0),
                bias_map_all=bias_map_all,
                grading_mode=str(grading_mode or "absolute"),
            )
        except Exception:
            try:
                self.metrics_widget.clear()
            except Exception:
                pass

    def _on_delete_processed_requested(self, file_path: str) -> None:
        if not file_path:
            return

        reply = QtWidgets.QMessageBox.question(
            self,
            "Confirm Delete",
            "Are you sure you want to delete this processed run?\nOnly this file will be deleted.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No
        )
        if reply == QtWidgets.QMessageBox.Yes:
            if self.controller:
                self.controller.delete_processed_run(file_path)

    def _on_run_clicked(self) -> None:
        payload = {
            "mode": "scalar",
            "device_id": self.device_combo.currentText().strip(),
            "csv_path": self.selected_test(),
            "slopes": {"x": float(self.spin_x.value()), "y": float(self.spin_y.value()), "z": float(self.spin_z.value())},
        }
        if self.controller:
            self.controller.run_processing(payload)
        else:
            self.run_requested.emit(payload)

    def _on_refresh_clicked(self) -> None:
        if self.controller:
            # Refresh the device list first (safe, does not trigger analysis).
            try:
                self.controller.refresh_devices()
            except Exception:
                pass
            # If a device is already selected, refresh its tests too.
            try:
                device_id = self.device_combo.currentText().strip()
            except Exception:
                device_id = ""
            if device_id:
                self.controller.refresh_tests(device_id)
        else:
            self.refresh_requested.emit()

    def _emit_test_changed(self) -> None:
        it = self.test_list.currentItem()
        path = str(it.data(QtCore.Qt.UserRole)) if it is not None else ""
        self.test_changed.emit(path)

    def _emit_processed_changed(self) -> None:
        it = self.processed_list.currentItem()
        data = dict(it.data(QtCore.Qt.UserRole)) if it is not None else {}
        if self.controller:
            self.controller.select_processed_run(data)
        self.processed_selected.emit(data)

    def _on_device_changed(self, text: str) -> None:
        device = str(text or "").strip()
        self.device_selected.emit(device)
        if not device:
            try:
                self.test_list.clear()
                self.processed_list.clear()
                self._clear_metrics()
            except Exception:
                pass
            try:
                self.test_changed.emit("")
            except Exception:
                pass
            return
        if self.controller:
            self.controller.refresh_tests(device)

    def _on_test_meta_loaded(self, meta: dict) -> None:
        if not isinstance(meta, dict):
            return
        bw = meta.get("body_weight_n")
        self.set_body_weight_n(bw if bw is not None else None)
        device = meta.get("device_id")
        if device:
            self.set_device_id(device)

    def _on_processing_status(self, payload: dict) -> None:
        payload = payload or {}
        status = str(payload.get("status") or "").lower()
        message = str(payload.get("message") or "Processing…")
        if status == "running":
            self._start_processing_ui(message)
        else:
            self._stop_processing_ui()
            if status == "error":
                try:
                    QtWidgets.QMessageBox.warning(self, "Temperature Processing", message)
                except Exception:
                    pass

    def _start_processing_ui(self, message: str) -> None:
        self._processing_text = message or "Processing…"
        self._processing_active = True
        self._spinner_index = 0
        self.btn_run.setEnabled(False)
        self._processing_timer.start()
        self.btn_run.setText(f"{self._spinner_frames[self._spinner_index]} {self._processing_text}")

    def _stop_processing_ui(self) -> None:
        if not self._processing_active:
            return
        self._processing_active = False
        self._processing_timer.stop()
        self.btn_run.setEnabled(True)
        self.btn_run.setText("Process")

    def _on_spinner_tick(self) -> None:
        if not self._processing_active:
            return
        self._spinner_index = (self._spinner_index + 1) % len(self._spinner_frames)
        self.btn_run.setText(f"{self._spinner_frames[self._spinner_index]} {self._processing_text}")

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        viewport = self.test_list.viewport() if self.test_list else None
        if obj in (self.test_list, viewport) and event.type() == QtCore.QEvent.Resize:
            self._refresh_test_labels()
        return super().eventFilter(obj, event)

    def _on_analysis_status(self, payload: dict) -> None:
        payload = payload or {}
        status = str(payload.get("status") or "").lower()
        message = str(payload.get("message") or "")
        if status == "running":
            self._start_analysis_spinner(message or "Analyzing…")
        else:
            self._stop_analysis_spinner(message if status == "error" else "")

    def _start_analysis_spinner(self, message: str) -> None:
        self._analysis_active = True
        self._analysis_index = 0
        self.analysis_status_label.setVisible(True)
        self.analysis_status_label.setText(f"{self._analysis_frames[self._analysis_index]} {message}")
        self._analysis_timer.start()

    def _stop_analysis_spinner(self, message: str = "") -> None:
        if not self._analysis_active:
            if message:
                self.analysis_status_label.setText(message)
                self.analysis_status_label.setVisible(True)
            else:
                self.analysis_status_label.setVisible(False)
            return
        self._analysis_active = False
        self._analysis_timer.stop()
        if message:
            self.analysis_status_label.setText(message)
            self.analysis_status_label.setVisible(True)
        else:
            self.analysis_status_label.setVisible(False)

    def _on_analysis_spinner_tick(self) -> None:
        if not self._analysis_active:
            return
        self._analysis_index = (self._analysis_index + 1) % len(self._analysis_frames)
        text = self.analysis_status_label.text()
        message = text.split(" ", 1)[1] if " " in text else ""
        self.analysis_status_label.setText(f"{self._analysis_frames[self._analysis_index]} {message or 'Analyzing…'}")

    # --- helpers -------------------------------------------------------------

    def _refresh_test_labels(self) -> None:
        if not self.test_list or self.test_list.count() == 0:
            return
        available = self._available_label_width()
        for idx in range(self.test_list.count()):
            item = self.test_list.item(idx)
            path = item.data(QtCore.Qt.UserRole)
            label = self._build_test_label(path, available)
            item.setText(label)

    def _available_label_width(self) -> Optional[int]:
        try:
            viewport = self.test_list.viewport()
            width = viewport.width()
        except Exception:
            return None
        if width <= 0:
            return None
        # Leave some breathing room for scrollbar/padding
        return max(0, width - 24)

    def _build_test_label(self, csv_path: Optional[str], available_px: Optional[int] = None) -> str:
        if not csv_path:
            return ""
        base_name = os.path.basename(csv_path.rstrip("\\/"))
        meta = self._load_meta_for_csv(csv_path)
        if not meta:
            return base_name

        temp_val = self._extract_temperature_value(meta)
        temp_text = f"{temp_val:.1f}°F" if temp_val is not None else "—°F"
        tester = str(meta.get("tester_name") or meta.get("tester") or "Unknown").strip() or "Unknown"
        prefix = f"{temp_text}, {tester}"

        date_text = self._format_test_date(meta.get("date"))
        if not date_text:
            return prefix

        metrics = self.test_list.fontMetrics() if self.test_list else None
        if not metrics or not available_px:
            filler_len = max(3, 48 - len(prefix) - len(date_text))
            filler = "." * filler_len
            return f"{prefix} {filler} {date_text}"

        dot_width = max(1, metrics.horizontalAdvance("."))
        prefix_width = metrics.horizontalAdvance(prefix + " ")
        suffix_width = metrics.horizontalAdvance(" " + date_text)
        filler_width = max(0, available_px - prefix_width - suffix_width)

        if filler_width <= 0:
            filler = "..."
        else:
            dot_count = max(3, filler_width // dot_width)
            filler = "." * int(dot_count)
        return f"{prefix} {filler} {date_text}"

    def _format_test_date(self, date_str: Optional[str]) -> str:
        if not date_str:
            return ""
        normalized = str(date_str).strip()
        if not normalized:
            return ""
        for fmt in ("%m-%d-%Y", "%Y-%m-%d", "%m/%d/%Y"):
            try:
                dt = datetime.datetime.strptime(normalized, fmt)
                return dt.strftime("%d/%m/%Y")
            except ValueError:
                continue
        return normalized.replace("-", "/")

    def _meta_path_for_csv(self, csv_path: str) -> str:
        base, _ext = os.path.splitext(csv_path)
        return f"{base}.meta.json"

    def _load_meta_for_csv(self, csv_path: str) -> Optional[dict]:
        meta_path = self._meta_path_for_csv(csv_path)
        if not os.path.isfile(meta_path):
            return None
        try:
            with open(meta_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return data
        except Exception:
            return None
        return None

    def _extract_temperature_value(self, meta: dict) -> Optional[float]:
        for key in ("room_temperature_f", "room_temp_f", "ambient_temp_f", "avg_temp"):
            value = meta.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None
