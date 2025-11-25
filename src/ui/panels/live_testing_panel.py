from __future__ import annotations

from typing import Optional

from PySide6 import QtCore, QtWidgets, QtGui

from ..state import ViewState


class _DiscreteTestDelegate(QtWidgets.QStyledItemDelegate):
    """Custom delegate to render discrete tests with left label, dotted leader, and right-aligned date."""

    def paint(self, painter: QtGui.QPainter, option: QtWidgets.QStyleOptionViewItem, index: QtCore.QModelIndex) -> None:  # type: ignore[override]
        # Let base class draw background/selection
        QtWidgets.QStyledItemDelegate.paint(self, painter, option, index)
        left = str(index.data(QtCore.Qt.UserRole + 1) or "")
        date = str(index.data(QtCore.Qt.UserRole + 2) or "")
        if not left and not date:
            return
        r = option.rect
        fm = option.fontMetrics
        painter.save()
        painter.setPen(option.palette.color(QtGui.QPalette.Text))
        # Vertical alignment
        baseline_y = r.top() + (r.height() + fm.ascent() - fm.descent()) // 2
        padding = 6
        left_x = r.left() + padding
        # Draw left label
        painter.drawText(left_x, baseline_y, left)
        left_w = fm.horizontalAdvance(left)
        # Draw right date
        date_w = fm.horizontalAdvance(date)
        right_x = r.right() - padding - date_w
        painter.drawText(right_x, baseline_y, date)
        # Draw dotted leader between
        dots_start_x = left_x + left_w + padding
        dots_end_x = right_x - padding
        dot_w = max(1, fm.horizontalAdvance("."))
        if dots_end_x > dots_start_x + dot_w:
            count = int((dots_end_x - dots_start_x) / dot_w)
            dots = "." * max(0, count)
            painter.drawText(dots_start_x, baseline_y, dots)
        painter.restore()


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
    # Discrete temperature testing actions
    discrete_new_requested = QtCore.Signal()
    discrete_add_requested = QtCore.Signal(str)
    discrete_test_selected = QtCore.Signal(str)
    plot_test_requested = QtCore.Signal()

    plot_test_requested = QtCore.Signal()

    def __init__(self, state: ViewState, controller: object = None, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.state = state
        self.controller = controller

        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(10)

        # Session Controls
        controls_box = QtWidgets.QGroupBox("Session Controls")
        controls_layout = QtWidgets.QVBoxLayout(controls_box)

        # Session type selector (Normal vs Temperature Test vs Discrete Temp.)
        mode_row = QtWidgets.QHBoxLayout()
        mode_row.addWidget(QtWidgets.QLabel("Session Type:"))
        self.session_mode_combo = QtWidgets.QComboBox()
        try:
            # Modes: Normal live test, continuous temperature test, and discrete temperature test
            self.session_mode_combo.addItems(["Normal", "Temperature Test", "Discrete Temp. Testing"])
        except Exception:
            pass
        mode_row.addWidget(self.session_mode_combo)
        mode_row.addStretch(1)
        controls_layout.addLayout(mode_row)

        # Discrete temp testing test picker (list, only visible in Discrete Temp. mode)
        discrete_picker_box = QtWidgets.QVBoxLayout()
        self.lbl_discrete_tests = QtWidgets.QLabel("Tests:")
        discrete_picker_box.addWidget(self.lbl_discrete_tests)
        self.discrete_test_list = QtWidgets.QListWidget()
        try:
            from PySide6 import QtWidgets as _QtW
            self.discrete_test_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            self.discrete_test_list.setUniformItemSizes(True)
            self.discrete_test_list.setSizePolicy(_QtW.QSizePolicy.Expanding, _QtW.QSizePolicy.Expanding)
            # Use custom delegate for dotted leader formatting
            self.discrete_test_list.setItemDelegate(_DiscreteTestDelegate(self.discrete_test_list))
        except Exception:
            pass
        discrete_picker_box.addWidget(self.discrete_test_list, 1)
        controls_layout.addLayout(discrete_picker_box)

        # Discrete temp testing actions (only visible in Discrete Temp. mode)
        discrete_row = QtWidgets.QHBoxLayout()
        self.btn_discrete_new = QtWidgets.QPushButton("Start New Test")
        self.btn_discrete_add = QtWidgets.QPushButton("Add to Existing Test")
        self.btn_discrete_add.setEnabled(False)
        try:
            # Make each button take half the width within the Session Controls group
            from PySide6 import QtWidgets as _QtW
            self.btn_discrete_new.setSizePolicy(_QtW.QSizePolicy.Expanding, _QtW.QSizePolicy.Fixed)
            self.btn_discrete_add.setSizePolicy(_QtW.QSizePolicy.Expanding, _QtW.QSizePolicy.Fixed)
        except Exception:
            pass
        discrete_row.addWidget(self.btn_discrete_new, 1)
        discrete_row.addWidget(self.btn_discrete_add, 1)
        controls_layout.addLayout(discrete_row)

        self.btn_start = QtWidgets.QPushButton("Start Session")
        self.btn_end = QtWidgets.QPushButton("End Session")
        self.btn_end.setEnabled(False)
        self.btn_next = QtWidgets.QPushButton("Next Stage")
        self.btn_next.setEnabled(False)

        stage_row = QtWidgets.QHBoxLayout()
        self.lbl_stage_title = QtWidgets.QLabel("Stage:")
        stage_row.addWidget(self.lbl_stage_title)
        self.stage_label = QtWidgets.QLabel("—")
        stage_row.addWidget(self.stage_label)
        stage_row.addStretch(1)

        progress_row = QtWidgets.QHBoxLayout()
        self.lbl_progress_title = QtWidgets.QLabel("Progress:")
        progress_row.addWidget(self.lbl_progress_title)
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
        self.cal_box = QtWidgets.QGroupBox("Calibration Heatmap")
        cal_layout = QtWidgets.QVBoxLayout(self.cal_box)
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
            self.heatmap_list.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
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
        cal_layout.addLayout(hm_row, 1)

        # Temps in Test: standalone pane to the right of Session Controls
        self.temps_box = QtWidgets.QGroupBox("Temps in Test")
        temps_layout = QtWidgets.QVBoxLayout(self.temps_box)
        temps_header = QtWidgets.QHBoxLayout()
        self.lbl_temps_baseline = QtWidgets.QLabel("Includes Baseline:")
        self.lbl_temps_baseline_icon = QtWidgets.QLabel("✖")
        temps_header.addWidget(self.lbl_temps_baseline)
        temps_header.addWidget(self.lbl_temps_baseline_icon)
        temps_header.addStretch(1)
        temps_layout.addLayout(temps_header)
        self.temps_list = QtWidgets.QListWidget()
        temps_layout.addWidget(self.temps_list, 1)
        # Plot button at bottom (enabled only when a test with data is selected)
        self.btn_plot_test = QtWidgets.QPushButton("Plot Test")
        self.btn_plot_test.setEnabled(False)
        temps_layout.addWidget(self.btn_plot_test)

        # Evenly distribute boxes side-by-side within a constrained-height tab page
        for w in (controls_box, self.temps_box, guide_box, meta_box, model_box, self.cal_box):
            try:
                w.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
            except Exception:
                pass
            root.addWidget(w, 1)

        if self.controller:
            self.btn_start.clicked.connect(lambda: self.start_session_requested.emit()) # Still emit for now, or call controller directly?
            # Let's keep the signal for now if ControlPanel uses it, but ControlPanel doesn't seem to use it.
            # ControlPanel just instantiates it.
            # So we should call controller directly.
            
            # We need to gather config for start_session.
            # This logic was previously in MainWindow.
            # We'll need a helper to gather config.
            pass
        
        self.btn_start.clicked.connect(self._on_start_clicked)
        self.btn_end.clicked.connect(self._on_end_clicked)
        self.btn_next.clicked.connect(self._on_next_clicked)
        self.btn_prev.clicked.connect(self._on_prev_clicked)
        
        self.btn_package_model.clicked.connect(lambda: self.package_model_requested.emit())
        self.btn_activate.clicked.connect(self._emit_activate)
        self.btn_deactivate.clicked.connect(self._emit_deactivate)
        self.btn_load_45v.clicked.connect(lambda: self.load_45v_requested.emit())
        self.btn_generate_heatmap.clicked.connect(lambda: self.generate_heatmap_requested.emit())
        self.heatmap_list.currentItemChanged.connect(self._on_heatmap_item_changed)
        self.heatmap_view_combo.currentTextChanged.connect(lambda s: self.heatmap_view_changed.emit(str(s)))
        
        # Connect Controller Signals
        if self.controller:
            self.controller.view_session_started.connect(self._on_session_started)
            self.controller.view_session_ended.connect(self._on_session_ended)
            self.controller.view_stage_changed.connect(self._on_stage_changed)
            self.controller.view_grid_configured.connect(self.configure_grid)

        # Discrete temp testing hooks
        try:
            self.session_mode_combo.currentTextChanged.connect(self._on_session_mode_changed)
            self.discrete_test_list.currentItemChanged.connect(self._on_discrete_test_changed)
            self.btn_discrete_new.clicked.connect(lambda: self.discrete_new_requested.emit())
            self.btn_discrete_add.clicked.connect(self._emit_discrete_add)
            self.btn_plot_test.clicked.connect(lambda: self.plot_test_requested.emit())
        except Exception:
            pass

        # Initialize visibility for session controls based on default mode
        self._update_session_controls_for_mode()

    def _is_discrete_temp_session(self) -> bool:
        """Return True if the current session type is Discrete Temp. Testing."""
        try:
            text = str(self.session_mode_combo.currentText() or "")
        except Exception:
            text = ""
        return text.strip().lower().startswith("discrete")

    def _update_session_controls_for_mode(self) -> None:
        """Show/hide controls depending on the selected session type."""
        is_discrete = self._is_discrete_temp_session()
        show_standard = not is_discrete
        try:
            # Standard live testing controls
            self.btn_start.setVisible(show_standard)
            self.btn_end.setVisible(show_standard)
            self.btn_prev.setVisible(show_standard)
            self.btn_next.setVisible(show_standard)
            self.lbl_stage_title.setVisible(show_standard)
            self.stage_label.setVisible(show_standard)
            self.lbl_progress_title.setVisible(show_standard)
            self.progress_label.setVisible(show_standard)
        except Exception:
            pass
        try:
            # Discrete temp testing controls
            self.lbl_discrete_tests.setVisible(is_discrete)
            self.discrete_test_list.setVisible(is_discrete)
            self.btn_discrete_new.setVisible(is_discrete)
            self.btn_discrete_add.setVisible(is_discrete)
        except Exception:
            pass
        # Toggle Temps-in-Test pane and Calibration Heatmap based on mode
        try:
            if hasattr(self, "temps_box"):
                self.temps_box.setVisible(is_discrete)
        except Exception:
            pass
        try:
            if hasattr(self, "cal_box"):
                self.cal_box.setVisible(not is_discrete)
        except Exception:
            pass
        # Reset add button enabled state whenever mode changes
        if not is_discrete:
            try:
                self.btn_discrete_add.setEnabled(False)
            except Exception:
                pass

    def _on_session_mode_changed(self, _text: str) -> None:
        self._update_session_controls_for_mode()

    def _on_discrete_test_changed(self, current: Optional[QtWidgets.QListWidgetItem], _previous: Optional[QtWidgets.QListWidgetItem]) -> None:
        # Enable Add button only when a valid test is selected
        has_selection = current is not None
        try:
            self.btn_discrete_add.setEnabled(bool(has_selection and self._is_discrete_temp_session()))
        except Exception:
            pass
        # Emit selection for Temps-in-Test view
        try:
            if has_selection and current is not None:
                key = current.data(QtCore.Qt.UserRole)
                if key:
                    self.discrete_test_selected.emit(str(key))
            else:
                # No selection: clear Temps-in-Test UI
                self.discrete_test_selected.emit("")
        except Exception:
            pass

    def _emit_discrete_add(self) -> None:
        # Emit currently selected test key (if any)
        try:
            item = self.discrete_test_list.currentItem()
            if item is None:
                return
            key = item.data(QtCore.Qt.UserRole)
            if key:
                self.discrete_add_requested.emit(str(key))
        except Exception:
            pass

    def set_discrete_tests(self, tests: list[tuple[str, str, str]]) -> None:
        """Populate discrete test picker with (label, date, key) triples."""
        try:
            self.discrete_test_list.blockSignals(True)
        except Exception:
            pass
        try:
            self.discrete_test_list.clear()
            for label, date_str, key in (tests or []):
                item = QtWidgets.QListWidgetItem()
                try:
                    item.setData(QtCore.Qt.UserRole, str(key))
                    item.setData(QtCore.Qt.UserRole + 1, str(label))
                    item.setData(QtCore.Qt.UserRole + 2, str(date_str))
                except Exception:
                    pass
                self.discrete_test_list.addItem(item)
        except Exception:
            pass
        finally:
            try:
                self.discrete_test_list.blockSignals(False)
            except Exception:
                pass
        # After repopulating, recompute add button enabled state
        try:
            current = self.discrete_test_list.currentItem()
        except Exception:
            current = None
        self._on_discrete_test_changed(current, None)

    def is_temperature_session(self) -> bool:
        """Return True if the current session type is Temperature Test."""
        try:
            text = str(self.session_mode_combo.currentText() or "")
        except Exception:
            text = ""
        return text.strip().lower().startswith("temperature")

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

    # Temps-in-Test tab helpers
    def set_temps_in_test(self, includes_baseline: bool | None, temps_f: list[float]) -> None:
        """Update the Temps in Test tab with baseline indicator and temperature list."""
        try:
            if includes_baseline is None:
                # No selection: clear icon and style
                self.lbl_temps_baseline_icon.setText("")
                self.lbl_temps_baseline_icon.setStyleSheet("")
            elif includes_baseline:
                self.lbl_temps_baseline_icon.setText("✔")
                self.lbl_temps_baseline_icon.setStyleSheet("color: #3CB371;")  # green
            else:
                self.lbl_temps_baseline_icon.setText("✖")
                self.lbl_temps_baseline_icon.setStyleSheet("color: #CC4444;")  # red
        except Exception:
            pass
        # Enable Plot button only when a test is selected and there is at least one session (including baseline-only)
        try:
            has_data = includes_baseline is True or bool(temps_f)
            self.btn_plot_test.setEnabled(bool(has_data))
        except Exception:
            self.btn_plot_test.setEnabled(False)
        try:
            self.temps_list.clear()
            for t in temps_f or []:
                try:
                    label = f"{float(t):.1f} °F"
                except Exception:
                    label = str(t)
                self.temps_list.addItem(label)
        except Exception:
            pass

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
        except Exception:
            pass

    def current_heatmap_view(self) -> str:
        try:
            return str(self.heatmap_view_combo.currentText() or "Heatmap")
        except Exception:
            return "Heatmap"

    def _on_start_clicked(self):
        if self.controller:
            # Gather config
            # For now, we use placeholders or get from UI if available
            # In the original app, MainWindow gathered this from state/config.
            # We might need to access state here.
            config = {
                'tester': "Tester", # Placeholder
                'device_id': self.lbl_device.text(),
                'model_id': self.lbl_model.text(),
                'body_weight_n': 0.0, # Placeholder
                'thresholds': None, # Placeholder
                'is_temp_test': self.is_temperature_session(),
                'is_discrete_temp': self._is_discrete_temp_session()
            }
            self.controller.start_session(config)
        else:
            self.start_session_requested.emit()

    def _on_end_clicked(self):
        if self.controller:
            self.controller.end_session()
        else:
            self.end_session_requested.emit()

    def _on_next_clicked(self):
        if self.controller:
            self.controller.next_stage()
        else:
            self.next_stage_requested.emit()

    def _on_prev_clicked(self):
        if self.controller:
            self.controller.prev_stage()
        else:
            self.previous_stage_requested.emit()

    def _on_session_started(self, session):
        self.btn_start.setEnabled(False)
        self.btn_end.setEnabled(True)
        self.btn_next.setEnabled(True)
        self.btn_prev.setEnabled(True)
        # Update other UI elements from session if needed

    def _on_session_ended(self):
        self.btn_start.setEnabled(True)
        self.btn_end.setEnabled(False)
        self.btn_next.setEnabled(False)
        self.btn_prev.setEnabled(False)
        self.stage_label.setText("—")
        self.progress_label.setText("0 / 0 cells")

    def _on_stage_changed(self, index, stage):
        self.stage_label.setText(stage.name)
        # Update progress label if stage has info
        total = stage.total_cells
        self.progress_label.setText(f"0 / {total} cells")
        # Update guide
        self.set_stage_progress(stage.name, 0, total)
