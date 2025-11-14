from __future__ import annotations

from typing import Optional

from PySide6 import QtCore, QtWidgets

from ..state import ViewState


class LiveTestingPanel(QtWidgets.QWidget):
    start_session_requested = QtCore.Signal()
    end_session_requested = QtCore.Signal()
    next_stage_requested = QtCore.Signal()
    previous_stage_requested = QtCore.Signal()
    package_model_requested = QtCore.Signal()
    activate_model_requested = QtCore.Signal(str)
    deactivate_model_requested = QtCore.Signal(str)
    load_45v_requested = QtCore.Signal()
    generate_heatmap_requested = QtCore.Signal()
    heatmap_selected = QtCore.Signal(str)
    heatmap_view_changed = QtCore.Signal(str)

    def __init__(self, state: ViewState, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.state = state

        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(10)

        # Session Controls
        controls_box = QtWidgets.QGroupBox("Session Controls")
        controls_layout = QtWidgets.QVBoxLayout(controls_box)

        self.btn_start = QtWidgets.QPushButton("Start Session")
        self.btn_end = QtWidgets.QPushButton("End Session")
        self.btn_end.setEnabled(False)
        self.btn_next = QtWidgets.QPushButton("Next Stage")
        self.btn_next.setEnabled(False)

        stage_row = QtWidgets.QHBoxLayout()
        stage_row.addWidget(QtWidgets.QLabel("Stage:"))
        self.stage_label = QtWidgets.QLabel("—")
        stage_row.addWidget(self.stage_label)
        stage_row.addStretch(1)

        progress_row = QtWidgets.QHBoxLayout()
        progress_row.addWidget(QtWidgets.QLabel("Progress:"))
        self.progress_label = QtWidgets.QLabel("0 / 0 cells")
        progress_row.addWidget(self.progress_label)
        progress_row.addStretch(1)

        controls_layout.addWidget(self.btn_start)
        controls_layout.addWidget(self.btn_end)
        nav_row = QtWidgets.QHBoxLayout()
        self.btn_prev = QtWidgets.QPushButton("Previous Stage")
        nav_row.addWidget(self.btn_prev)
        nav_row.addWidget(self.btn_next)
        nav_row.addStretch(1)
        controls_layout.addLayout(nav_row)
        controls_layout.addLayout(stage_row)
        controls_layout.addLayout(progress_row)

        # Testing Guide
        guide_box = QtWidgets.QGroupBox("Testing Guide")
        guide_layout = QtWidgets.QVBoxLayout(guide_box)
        self.guide_label = QtWidgets.QLabel("Use Start Session to begin. Follow prompts here.")
        self.guide_label.setWordWrap(True)
        guide_layout.addWidget(self.guide_label)
        guide_layout.addStretch(1)

        # Session Info & Thresholds
        meta_box = QtWidgets.QGroupBox("Session Info & Thresholds")
        meta_layout = QtWidgets.QFormLayout(meta_box)
        self.lbl_tester = QtWidgets.QLabel("—")
        self.lbl_device = QtWidgets.QLabel("—")
        self.lbl_model = QtWidgets.QLabel("—")
        self.lbl_bw = QtWidgets.QLabel("—")
        meta_layout.addRow("Tester:", self.lbl_tester)
        meta_layout.addRow("Device ID:", self.lbl_device)
        meta_layout.addRow("Model ID:", self.lbl_model)
        meta_layout.addRow("Body Weight (N):", self.lbl_bw)
        self.lbl_thresh_db = QtWidgets.QLabel("—")
        self.lbl_thresh_bw = QtWidgets.QLabel("—")
        meta_layout.addRow("45 lb DB (±N):", self.lbl_thresh_db)
        meta_layout.addRow("Body Weight (±N):", self.lbl_thresh_bw)

        # Live Telemetry removed

        # Model (replaces Debug Status)
        model_box = QtWidgets.QGroupBox("Model")
        model_layout = QtWidgets.QVBoxLayout(model_box)

        # Current model row
        current_row = QtWidgets.QHBoxLayout()
        current_row.addWidget(QtWidgets.QLabel("Current Model:"))
        self.lbl_current_model = QtWidgets.QLabel("—")
        current_row.addWidget(self.lbl_current_model)
        current_row.addStretch(1)
        model_layout.addLayout(current_row)

        # Available models list
        model_layout.addWidget(QtWidgets.QLabel("Available Models:"))
        self.model_list = QtWidgets.QListWidget()
        self.model_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        try:
            self.model_list.setUniformItemSizes(True)
        except Exception:
            pass
        try:
            self.model_list.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
        except Exception:
            pass
        model_layout.addWidget(self.model_list, 1)

        # Status row
        status_row = QtWidgets.QHBoxLayout()
        self.lbl_model_status = QtWidgets.QLabel("")
        self.lbl_model_status.setStyleSheet("color:#ccc;")
        status_row.addWidget(self.lbl_model_status)
        status_row.addStretch(1)
        model_layout.addLayout(status_row)

        # Activate/Deactivate controls
        act_row = QtWidgets.QHBoxLayout()
        self.btn_activate = QtWidgets.QPushButton("Activate")
        self.btn_deactivate = QtWidgets.QPushButton("Deactivate")
        act_row.addWidget(self.btn_activate)
        act_row.addWidget(self.btn_deactivate)
        act_row.addStretch(1)
        model_layout.addLayout(act_row)

        # Package button
        self.btn_package_model = QtWidgets.QPushButton("Package Model…")
        model_layout.addWidget(self.btn_package_model)
        model_layout.addStretch(1)

        # Calibration Heatmap
        cal_box = QtWidgets.QGroupBox("Calibration Heatmap")
        cal_layout = QtWidgets.QVBoxLayout(cal_box)
        cal_row = QtWidgets.QHBoxLayout()
        cal_row.addWidget(QtWidgets.QLabel("Status:"))
        self.lbl_cal_status = QtWidgets.QLabel("—")
        cal_row.addWidget(self.lbl_cal_status)
        cal_row.addStretch(1)
        cal_layout.addLayout(cal_row)
        self.btn_load_45v = QtWidgets.QPushButton("Load Test Files…")
        self.btn_generate_heatmap = QtWidgets.QPushButton("Generate Heatmaps")
        self.btn_generate_heatmap.setEnabled(False)
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addWidget(self.btn_load_45v)
        btn_row.addWidget(self.btn_generate_heatmap)
        btn_row.addStretch(1)
        cal_layout.addLayout(btn_row)
        # View mode
        view_row = QtWidgets.QHBoxLayout()
        view_row.addWidget(QtWidgets.QLabel("View:"))
        self.heatmap_view_combo = QtWidgets.QComboBox()
        self.heatmap_view_combo.addItems(["Heatmap", "Grid View"])
        view_row.addWidget(self.heatmap_view_combo)
        view_row.addStretch(1)
        cal_layout.addLayout(view_row)

        # Metrics table
        self.metrics_table = QtWidgets.QTableWidget(5, 3)
        try:
            self.metrics_table.setHorizontalHeaderLabels(["Metric", "N", "%"])
            self.metrics_table.verticalHeader().setVisible(False)
            # Size columns/rows to contents for tighter look
            hh = self.metrics_table.horizontalHeader()
            vh = self.metrics_table.verticalHeader()
            try:
                hh.setStretchLastSection(False)
                from PySide6.QtWidgets import QHeaderView as _QHV
                hh.setSectionResizeMode(_QHV.ResizeToContents)
                vh.setSectionResizeMode(_QHV.ResizeToContents)
            except Exception:
                pass
            self.metrics_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            self.metrics_table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
            # Reduce grid and margins
            self.metrics_table.setShowGrid(False)
            self.metrics_table.setStyleSheet("QTableWidget { padding: 0px; } QTableWidget::item { padding: 2px 6px; }")
        except Exception:
            pass
        labels = ["Count", "Mean Error", "Median Error", "Max Error", "Bias (signed)"]
        for i, text in enumerate(labels):
            self.metrics_table.setItem(i, 0, QtWidgets.QTableWidgetItem(text))
            self.metrics_table.setItem(i, 1, QtWidgets.QTableWidgetItem("—"))
            self.metrics_table.setItem(i, 2, QtWidgets.QTableWidgetItem("—"))
        cal_layout.addWidget(self.metrics_table)
        try:
            # Minimize whitespace: compute tight height after sizing to contents
            self.metrics_table.resizeColumnsToContents()
            self.metrics_table.resizeRowsToContents()
            row_h = max(18, self.metrics_table.verticalHeader().defaultSectionSize())
            header_h = self.metrics_table.horizontalHeader().height()
            self.metrics_table.setFixedHeight(header_h + row_h * len(labels) + 2)
        except Exception:
            pass
        # Generated heatmaps list + Metrics side-by-side
        cal_layout.addWidget(QtWidgets.QLabel("Generated Heatmaps:"))
        self.heatmap_list = QtWidgets.QListWidget()
        try:
            self.heatmap_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        except Exception:
            pass
        # Place picker and metrics table side by side
        hm_row = QtWidgets.QHBoxLayout()
        # Left: heatmap picker (expands)
        try:
            self.heatmap_list.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        except Exception:
            pass
        hm_row.addWidget(self.heatmap_list, 2)
        # Right: metrics table (tight width)
        try:
            self.metrics_table.resizeColumnsToContents()
            self.metrics_table.resizeRowsToContents()
            self.metrics_table.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        except Exception:
            pass
        hm_row.addWidget(self.metrics_table, 0, QtCore.Qt.AlignTop)
        # Place directly after prior controls (no bottom pin)
        cal_layout.addLayout(hm_row)

        # Ensure tight sizing after layout settles
        try:
            QtCore.QTimer.singleShot(0, self._update_metrics_table_size)
        except Exception:
            pass

        # Evenly distribute boxes side-by-side within a constrained-height tab page
        for w in (controls_box, guide_box, meta_box, model_box, cal_box):
            try:
                w.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
            except Exception:
                pass
            root.addWidget(w, 1)

        self.btn_start.clicked.connect(lambda: self.start_session_requested.emit())
        self.btn_end.clicked.connect(lambda: self.end_session_requested.emit())
        self.btn_next.clicked.connect(lambda: self.next_stage_requested.emit())
        self.btn_package_model.clicked.connect(lambda: self.package_model_requested.emit())
        self.btn_activate.clicked.connect(self._emit_activate)
        self.btn_deactivate.clicked.connect(self._emit_deactivate)
        self.btn_prev.clicked.connect(lambda: self.previous_stage_requested.emit())
        self.btn_load_45v.clicked.connect(lambda: self.load_45v_requested.emit())
        self.btn_generate_heatmap.clicked.connect(lambda: self.generate_heatmap_requested.emit())
        self.heatmap_list.currentItemChanged.connect(self._on_heatmap_item_changed)
        self.heatmap_view_combo.currentTextChanged.connect(lambda s: self.heatmap_view_changed.emit(str(s)))

    # Overlay is now managed by the canvas; this panel keeps controls only
    def configure_grid(self, rows: int, cols: int) -> None:
        pass

    def set_active_cell(self, row: int | None, col: int | None) -> None:
        pass

    def set_cell_error_color(self, row: int, col: int, color: QtGui.QColor) -> None:
        pass

    # UI helpers for future wiring
    def set_metadata(self, tester: str, device_id: str, model_id: str, body_weight_n: float) -> None:
        self.lbl_tester.setText(tester or "—")
        self.lbl_device.setText(device_id or "—")
        self.lbl_model.setText(model_id or "—")
        try:
            self.lbl_bw.setText(f"{body_weight_n:.1f}")
        except Exception:
            self.lbl_bw.setText("—")

    def set_session_model_id(self, model_id: str | None) -> None:
        # Keep Session Info pane's Model ID in sync with active model selection
        self.lbl_model.setText((model_id or "").strip() or "—")

    def set_thresholds(self, db_tol_n: float, bw_tol_n: float) -> None:
        try:
            self.lbl_thresh_db.setText(f"±{db_tol_n:.1f}")
        except Exception:
            self.lbl_thresh_db.setText("—")
        try:
            self.lbl_thresh_bw.setText(f"±{bw_tol_n:.1f}")
        except Exception:
            self.lbl_thresh_bw.setText("—")

    def set_stage_progress(self, stage_text: str, completed_cells: int, total_cells: int) -> None:
        self.stage_label.setText(stage_text)
        self.progress_label.setText(f"{completed_cells} / {total_cells} cells")
        # Update guide text
        try:
            self.guide_label.setText(
                f"{stage_text}\n\n"
                "Instructions:\n"
                "- Place the specified load in any cell.\n"
                "- Keep COP inside the cell until stable (≈2s, Fz steady).\n"
                "- When captured, the cell will colorize. Move to next cell.\n"
                "- After all cells, follow prompts for the next stage/location."
            )
        except Exception:
            pass

    def set_next_stage_enabled(self, enabled: bool) -> None:
        try:
            self.btn_next.setEnabled(bool(enabled))
        except Exception:
            pass

    def set_next_stage_label(self, text: str) -> None:
        try:
            self.btn_next.setText(text or "Next Stage")
        except Exception:
            pass

    def set_telemetry(self, fz_n: Optional[float], cop_x_mm: Optional[float], cop_y_mm: Optional[float], stability_text: str) -> None:
        # Live telemetry UI removed; keep as no-op for compatibility
        return

    def set_current_model(self, model_text: Optional[str]) -> None:
        self.lbl_current_model.setText((model_text or "").strip() or "—")
        # Clear transient status when model label changes externally
        self.set_model_status("")

    def set_model_list(self, models: list[dict]) -> None:
        # Populate the list with modelId and optional location annotation
        try:
            self.model_list.clear()
            for m in (models or []):
                try:
                    mid = str((m or {}).get("modelId") or (m or {}).get("model_id") or "").strip()
                except Exception:
                    mid = ""
                if not mid:
                    continue
                loc = str((m or {}).get("location") or "").strip()
                # Format package date if present (ms or s) as MM.DD.YYYY
                date_text = ""
                try:
                    import datetime
                    raw_ts = (m or {}).get("packageDate") or (m or {}).get("package_date")
                    if raw_ts is not None:
                        ts = float(raw_ts)
                        if ts > 1e12:
                            ts = ts / 1000.0
                        dt = datetime.datetime.fromtimestamp(ts)
                        date_text = dt.strftime("%m.%d.%Y")
                except Exception:
                    date_text = ""
                # Build concise label: id (location) • date
                if loc and date_text:
                    text = f"{mid}  ({loc}) • {date_text}"
                elif loc:
                    text = f"{mid}  ({loc})"
                elif date_text:
                    text = f"{mid}  • {date_text}"
                else:
                    text = mid
                item = QtWidgets.QListWidgetItem(text)
                # Store raw id for reliable retrieval
                item.setData(QtCore.Qt.UserRole, mid)
                self.model_list.addItem(item)
        except Exception:
            pass

    def set_model_status(self, text: Optional[str]) -> None:
        self.lbl_model_status.setText((text or "").strip())

    def set_model_controls_enabled(self, enabled: bool) -> None:
        try:
            self.btn_activate.setEnabled(bool(enabled))
            self.btn_deactivate.setEnabled(bool(enabled))
            self.btn_package_model.setEnabled(bool(enabled))
        except Exception:
            pass

    def set_debug_status(self, text: str | None) -> None:
        # Debug status deprecated in favor of Model panel; keep as no-op to avoid breaking call sites
        return

    # No stage selector UI anymore; navigation is via Previous/Next buttons

    def _emit_activate(self) -> None:
        # Use selected model from list; fall back to current label
        try:
            item = self.model_list.currentItem()
            mid = (item.data(QtCore.Qt.UserRole) if item is not None else None) or (self.lbl_current_model.text() or "").strip()
        except Exception:
            mid = (self.lbl_current_model.text() or "").strip()
        if mid and mid != "—" and not str(mid).lower().startswith("loading"):
            self.set_model_status("Activating…")
            self.set_model_controls_enabled(False)
            self.activate_model_requested.emit(str(mid))

    def _emit_deactivate(self) -> None:
        mid = (self.lbl_current_model.text() or "").strip()
        if mid and mid != "—" and not mid.lower().startswith("loading"):
            self.set_model_status("Deactivating…")
            self.set_model_controls_enabled(False)
            self.deactivate_model_requested.emit(mid)


    # --- Calibration Heatmap helpers ---
    def set_calibration_enabled(self, enabled: bool) -> None:
        try:
            self.btn_load_45v.setEnabled(bool(enabled))
            # Generate only enabled when a file is loaded; default off here
        except Exception:
            pass

    def set_calibration_status(self, text: Optional[str]) -> None:
        try:
            self.lbl_cal_status.setText((text or "").strip() or "—")
        except Exception:
            pass

    def set_generate_enabled(self, enabled: bool) -> None:
        try:
            self.btn_generate_heatmap.setEnabled(bool(enabled))
        except Exception:
            pass

    # --- Heatmap list API ---
    def add_heatmap_entry(self, label: str, key: str, count: int) -> None:
        try:
            text = f"{label}  ({count})"
            item = QtWidgets.QListWidgetItem(text)
            item.setData(QtCore.Qt.UserRole, str(key))
            self.heatmap_list.addItem(item)
        except Exception:
            pass

    def clear_heatmap_entries(self) -> None:
        try:
            self.heatmap_list.clear()
        except Exception:
            pass

    def _on_heatmap_item_changed(self, current: Optional[QtWidgets.QListWidgetItem], _previous: Optional[QtWidgets.QListWidgetItem]) -> None:
        if current is None:
            return
        try:
            key = current.data(QtCore.Qt.UserRole)
            if key:
                self.heatmap_selected.emit(str(key))
        except Exception:
            pass

    def set_heatmap_metrics(self, metrics: dict, is_all: bool) -> None:
        try:
            # metrics keys:
            # count, mean_err, median_err, max_err (N)
            # mean_pct, median_pct, max_pct, signed_bias_pct (%)
            count = int(metrics.get("count", 0))
            # Column 1 (N) values
            if not is_all:
                n_vals = [
                    str(count),
                    f"{float(metrics.get('mean_err', 0.0)):.1f}",
                    f"{float(metrics.get('median_err', 0.0)):.1f}",
                    f"{float(metrics.get('max_err', 0.0)):.1f}",
                    "—",  # Bias has no N
                ]
            else:
                n_vals = [str(count), "—", "—", "—", "—"]
            # Column 2 (%) values
            pct_vals = [
                "—",
                f"{float(metrics.get('mean_pct', 0.0)):.1f}",
                f"{float(metrics.get('median_pct', 0.0)):.1f}",
                f"{float(metrics.get('max_pct', 0.0)):.1f}",
                f"{float(metrics.get('signed_bias_pct', 0.0)):.1f}",
            ]
            for i, v in enumerate(n_vals):
                self.metrics_table.setItem(i, 1, QtWidgets.QTableWidgetItem(v))
            for i, v in enumerate(pct_vals):
                self.metrics_table.setItem(i, 2, QtWidgets.QTableWidgetItem(v))
            # Recompute tight size based on current content
            self._update_metrics_table_size()
        except Exception:
            pass

    def current_heatmap_view(self) -> str:
        try:
            return str(self.heatmap_view_combo.currentText() or "Heatmap")
        except Exception:
            return "Heatmap"

    # --- Internal layout helpers ---
    def _update_metrics_table_size(self) -> None:
        try:
            # Columns sized to contents; compute tight total width
            self.metrics_table.resizeColumnsToContents()
            self.metrics_table.resizeRowsToContents()
            hh = self.metrics_table.horizontalHeader()
            total_w = 0
            for c in range(self.metrics_table.columnCount()):
                total_w += hh.sectionSize(c)
            # Account for table frame and a small padding
            total_w += max(8, self.metrics_table.frameWidth() * 2 + 6)
            self.metrics_table.setFixedWidth(int(total_w))
            # Compute tight height for all rows visible
            header_h = self.metrics_table.horizontalHeader().height()
            row_h_total = 0
            for r in range(self.metrics_table.rowCount()):
                row_h_total += self.metrics_table.rowHeight(r)
            content_h = header_h + row_h_total + 2
            self.metrics_table.setFixedHeight(int(content_h))
            # Match picker height to metrics table to avoid tug-of-war
            self.heatmap_list.setFixedHeight(int(content_h))
        except Exception:
            pass

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        try:
            self._update_metrics_table_size()
        except Exception:
            pass

