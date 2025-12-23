from __future__ import annotations

from typing import Optional

from PySide6 import QtCore, QtWidgets, QtGui

from ..state import ViewState
from .live_testing.session_controls_box import SessionControlsBox
from .live_testing.testing_guide_box import TestingGuideBox
from .live_testing.session_info_box import SessionInfoBox
from .live_testing.model_box import ModelBox
from .live_testing.calibration_heatmap_box import CalibrationHeatmapBox
from .live_testing.temps_in_test_box import TempsInTestBox


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
    process_test_requested = QtCore.Signal()

    def __init__(self, state: ViewState, controller: object = None, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.state = state
        self.controller = controller

        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(10)

        # Keep references to sub-boxes (logic lives in the boxes; panel remains public API facade)
        self._controls_box: SessionControlsBox
        self._temps_box: TempsInTestBox
        self._guide_box: TestingGuideBox
        self._meta_box: SessionInfoBox
        self._model_box: ModelBox
        self._cal_box: CalibrationHeatmapBox

        # Build UI from small focused group boxes, then bind widgets onto this instance
        controls_box = SessionControlsBox(self)
        temps_box = TempsInTestBox(self)
        guide_box = TestingGuideBox(self)
        meta_box = SessionInfoBox(self)
        model_box = ModelBox(self)
        self.cal_box = CalibrationHeatmapBox(self)

        self._controls_box = controls_box
        self._temps_box = temps_box
        self._guide_box = guide_box
        self._meta_box = meta_box
        self._model_box = model_box
        self._cal_box = self.cal_box

        # Back-compat bindings (attributes referenced by existing methods)
        # Session controls / discrete picker
        self.session_mode_combo = controls_box.session_mode_combo
        self.discrete_test_list = controls_box.discrete_test_list
        self.btn_discrete_new = controls_box.btn_discrete_new
        self.btn_discrete_add = controls_box.btn_discrete_add
        self.discrete_type_filter = controls_box.discrete_type_filter
        self.discrete_plate_filter = controls_box.discrete_plate_filter
        self.discrete_type_label = controls_box.discrete_type_label
        self.discrete_plate_label = controls_box.discrete_plate_label
        self.btn_start = controls_box.btn_start
        self.btn_end = controls_box.btn_end
        self.btn_next = controls_box.btn_next
        self.btn_prev = controls_box.btn_prev
        self.lbl_stage_title = controls_box.lbl_stage_title
        self.stage_label = controls_box.stage_label
        self.lbl_progress_title = controls_box.lbl_progress_title
        self.progress_label = controls_box.progress_label

        # Temps-in-test
        self.temps_box = temps_box
        self.lbl_temps_baseline = temps_box.lbl_temps_baseline
        self.lbl_temps_baseline_icon = temps_box.lbl_temps_baseline_icon
        self.temps_list = temps_box.temps_list
        self.btn_plot_test = temps_box.btn_plot_test
        self.btn_process_test = temps_box.btn_process_test

        # Guide
        self.guide_label = guide_box.guide_label

        # Session info/meta
        self.lbl_tester = meta_box.lbl_tester
        self.lbl_device = meta_box.lbl_device
        self.lbl_model = meta_box.lbl_model
        self.lbl_bw = meta_box.lbl_bw
        self.lbl_test_date_title = meta_box.lbl_test_date_title
        self.lbl_test_date = meta_box.lbl_test_date
        self.lbl_short_label_title = meta_box.lbl_short_label_title
        self.lbl_short_label = meta_box.lbl_short_label
        self.lbl_thresh_db = meta_box.lbl_thresh_db
        self.lbl_thresh_bw = meta_box.lbl_thresh_bw

        # Model panel
        self.lbl_current_model = model_box.lbl_current_model
        self.model_list = model_box.model_list
        self.lbl_model_status = model_box.lbl_model_status
        self.btn_activate = model_box.btn_activate
        self.btn_deactivate = model_box.btn_deactivate
        self.btn_package_model = model_box.btn_package_model

        # Calibration heatmap
        self.lbl_cal_status = self.cal_box.lbl_cal_status
        self.btn_load_45v = self.cal_box.btn_load_45v
        self.btn_generate_heatmap = self.cal_box.btn_generate_heatmap
        self.heatmap_view_combo = self.cal_box.heatmap_view_combo
        self.heatmap_list = self.cal_box.heatmap_list
        self.metrics_table = self.cal_box.metrics_table

        for w in (controls_box, temps_box, guide_box, meta_box, model_box, self.cal_box):
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
            # Discrete temp test lists + temps-in-test
            self.controller.discrete_tests_listed.connect(self.set_discrete_tests)
            self.controller.discrete_temps_updated.connect(self.set_temps_in_test)

        # Discrete temp testing hooks
        try:
            self.session_mode_combo.currentTextChanged.connect(self._on_session_mode_changed)
            self.discrete_test_list.currentItemChanged.connect(self._on_discrete_test_changed)
            self.btn_discrete_new.clicked.connect(lambda: self.discrete_new_requested.emit())
            self.btn_discrete_add.clicked.connect(self._emit_discrete_add)
            self.btn_plot_test.clicked.connect(lambda: self.plot_test_requested.emit())
            self.btn_process_test.clicked.connect(lambda: self.process_test_requested.emit())
            self.discrete_type_filter.currentTextChanged.connect(lambda _s: self._apply_discrete_filters())
            self.discrete_plate_filter.currentTextChanged.connect(lambda _s: self._apply_discrete_filters())
            # Forward selected test path to controller for analysis
            if self.controller:
                self.discrete_test_selected.connect(self.controller.on_discrete_test_selected)
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
            # Discrete temp testing controls (filters + list + buttons)
            self.discrete_test_list.setVisible(is_discrete)
            self.btn_discrete_new.setVisible(is_discrete)
            self.btn_discrete_add.setVisible(is_discrete)
            self.discrete_type_filter.setVisible(is_discrete)
            self.discrete_plate_filter.setVisible(is_discrete)
            # Also hide labels when not in discrete mode
            self.discrete_type_label.setVisible(is_discrete)
            self.discrete_plate_label.setVisible(is_discrete)
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
        # Show/hide discrete test meta fields in Session Info
        try:
            if hasattr(self, "lbl_test_date_title"):
                self.lbl_test_date_title.setVisible(is_discrete)
                self.lbl_test_date.setVisible(is_discrete)
            if hasattr(self, "lbl_short_label_title"):
                self.lbl_short_label_title.setVisible(is_discrete)
                self.lbl_short_label.setVisible(is_discrete)
        except Exception:
            pass
        # Process button moved to Temp Coefs tab; keep this hidden to avoid confusion.
        try:
            if hasattr(self, "btn_process_test"):
                self.btn_process_test.setVisible(False)
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
        if self._is_discrete_temp_session() and self.controller:
            self.controller.refresh_discrete_tests()

    def _on_discrete_test_changed(self, current: Optional[QtWidgets.QListWidgetItem], _previous: Optional[QtWidgets.QListWidgetItem]) -> None:
        # Enable Add button only when a valid test is selected
        has_selection = current is not None
        try:
            self.btn_discrete_add.setEnabled(bool(has_selection and self._is_discrete_temp_session()))
        except Exception:
            pass
        # Populate Session Info pane from test_meta.json when in discrete mode
        try:
            if self._is_discrete_temp_session():
                key = str(current.data(QtCore.Qt.UserRole)) if (has_selection and current is not None) else ""
                self._meta_box.apply_discrete_test_meta(key)
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

    def _apply_discrete_test_meta(self, key: str) -> None:
        # Backwards-compatible wrapper
        self._meta_box.apply_discrete_test_meta(key)

    def _emit_discrete_add(self) -> None:
        key = self._controls_box.current_discrete_test_key()
        if key:
            self.discrete_add_requested.emit(str(key))

    def set_discrete_tests(self, tests: list[tuple[str, str, str]]) -> None:
        self._controls_box.set_discrete_tests(tests)
        # Refresh add button enabled state
        try:
            current = self.discrete_test_list.currentItem()
        except Exception:
            current = None
        self._on_discrete_test_changed(current, None)

    def _apply_discrete_filters(self) -> None:
        self._controls_box.apply_discrete_filters()
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
        self._guide_box.set_stage_progress(stage_text, completed_cells, total_cells)

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
        self._model_box.set_current_model(model_text)

    def set_model_list(self, models: list[dict]) -> None:
        self._model_box.set_model_list(models)

    def set_model_status(self, text: Optional[str]) -> None:
        self._model_box.set_model_status(text)

    def set_model_controls_enabled(self, enabled: bool) -> None:
        self._model_box.set_model_controls_enabled(enabled)

    def set_debug_status(self, text: str | None) -> None:
        # Debug status deprecated in favor of Model panel; keep as no-op to avoid breaking call sites
        return

    # Temps-in-Test tab helpers
    def set_temps_in_test(self, includes_baseline: bool | None, temps_f: list[float]) -> None:
        self._temps_box.set_temps_in_test(includes_baseline, temps_f)

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
        self._cal_box.set_calibration_enabled(enabled)

    def set_calibration_status(self, text: Optional[str]) -> None:
        self._cal_box.set_calibration_status(text)

    def set_generate_enabled(self, enabled: bool) -> None:
        self._cal_box.set_generate_enabled(enabled)

    # --- Heatmap list API ---
    def add_heatmap_entry(self, label: str, key: str, count: int) -> None:
        self._cal_box.add_heatmap_entry(label, key, count)

    def clear_heatmap_entries(self) -> None:
        self._cal_box.clear_heatmap_entries()

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
        self._cal_box.set_heatmap_metrics(metrics, is_all)

    def current_heatmap_view(self) -> str:
        return self._cal_box.current_heatmap_view()

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
