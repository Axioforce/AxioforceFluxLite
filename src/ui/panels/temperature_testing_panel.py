from __future__ import annotations

import datetime
import json
import os
from typing import Optional

from PySide6 import QtCore, QtWidgets


class TemperatureTestingPanel(QtWidgets.QWidget):
    run_requested = QtCore.Signal(dict)
    device_selected = QtCore.Signal(str)
    refresh_requested = QtCore.Signal()
    test_changed = QtCore.Signal(str)
    processed_selected = QtCore.Signal(object)  # dict with slopes/paths
    stage_changed = QtCore.Signal(str)
    plot_stages_requested = QtCore.Signal()  # Request matplotlib stage visualization

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

        # Slopes
        slopes_row = 2
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

        # Run button
        self.btn_run = QtWidgets.QPushButton("Process")

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
        controls_layout.addStretch(1)
        
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

        self.device_combo.currentTextChanged.connect(self._on_device_changed)
        self.btn_refresh.clicked.connect(self._on_refresh_clicked)
        self.btn_run.clicked.connect(self._on_run_clicked)
        self.test_list.currentItemChanged.connect(self._emit_test_changed)
        self.processed_list.currentItemChanged.connect(self._emit_processed_changed)
        self.stage_combo.currentTextChanged.connect(lambda s: self.stage_changed.emit(str(s)))
        self.btn_plot_stages.clicked.connect(lambda: self.plot_stages_requested.emit())

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
            self.test_changed.connect(self.controller.load_test_details)
            
            # Initial fetch
            self.controller.refresh_devices()

    def set_devices(self, devices: list[str]) -> None:
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        self.device_combo.addItems(devices)
        self.device_combo.blockSignals(False)
        if devices:
            self._on_device_changed(self.device_combo.currentText())

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
        if self.test_list.count() > 0:
            self.test_list.setCurrentRow(0)
        else:
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
            it = QtWidgets.QListWidgetItem(str(label))
            it.setData(QtCore.Qt.UserRole, dict(e))
            self.processed_list.addItem(it)
        if self.processed_list.count() > 0:
            last_idx = self.processed_list.count() - 1
            self.processed_list.setCurrentRow(last_idx)
            self._emit_processed_changed()

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

    def set_analysis_metrics(self, payload: dict) -> None:
        """Update the metrics labels from analysis results."""
        if not payload:
            self._clear_metrics()
            return
        
        stage_key = self.current_stage()
        baseline = payload.get("baseline", {})
        selected = payload.get("selected", {})
        
        base_stats = self._compute_stage_stats(baseline, stage_key)
        sel_stats = self._compute_stage_stats(selected, stage_key)
        
        # Update baseline labels
        self.lbl_base_cnt.setText(str(base_stats.get("count", 0)))
        self.lbl_base_mean.setText(f"{base_stats.get('mean_pct', 0.0):.2f}%")
        self.lbl_base_med.setText(f"{base_stats.get('median_pct', 0.0):.2f}%")
        self.lbl_base_max.setText(f"{base_stats.get('max_pct', 0.0):.2f}%")
        
        # Update selected labels
        self.lbl_sel_cnt.setText(str(sel_stats.get("count", 0)))
        self.lbl_sel_mean.setText(f"{sel_stats.get('mean_pct', 0.0):.2f}%")
        self.lbl_sel_med.setText(f"{sel_stats.get('median_pct', 0.0):.2f}%")
        self.lbl_sel_max.setText(f"{sel_stats.get('max_pct', 0.0):.2f}%")

    def _compute_stage_stats(self, data: dict, stage_key: str) -> dict:
        """Compute aggregate stats for a stage or all stages."""
        stages = data.get("stages", {})
        pcts: list[float] = []
        
        if stage_key == "All":
            # Gather all cells from all stages
            for stage_data in stages.values():
                for cell in stage_data.get("cells", []):
                    pcts.append(abs(float(cell.get("signed_pct", 0.0))))
        else:
            stage_data = stages.get(stage_key, {})
            for cell in stage_data.get("cells", []):
                pcts.append(abs(float(cell.get("signed_pct", 0.0))))
        
        if not pcts:
            return {"count": 0, "mean_pct": 0.0, "median_pct": 0.0, "max_pct": 0.0}
        
        pcts_sorted = sorted(pcts)
        n = len(pcts_sorted)
        median = pcts_sorted[n // 2] if n % 2 == 1 else (pcts_sorted[n // 2 - 1] + pcts_sorted[n // 2]) / 2.0
        
        return {
            "count": n,
            "mean_pct": sum(pcts) / n,
            "median_pct": median,
            "max_pct": max(pcts),
        }

    def _clear_metrics(self) -> None:
        """Reset all metrics labels to default."""
        for lbl in (self.lbl_base_cnt, self.lbl_base_mean, self.lbl_base_med, self.lbl_base_max,
                    self.lbl_sel_cnt, self.lbl_sel_mean, self.lbl_sel_med, self.lbl_sel_max):
            lbl.setText("—")

    def _on_run_clicked(self) -> None:
        payload = {
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
            device_id = self.device_combo.currentText().strip()
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
