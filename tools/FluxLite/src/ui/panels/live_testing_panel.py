from __future__ import annotations

from typing import Optional

from PySide6 import QtCore, QtWidgets, QtGui

from ..state import ViewState
from .live_testing.session_controls_box import SessionControlsBox
from .live_testing.testing_guide_box import TestingGuideBox
from .live_testing.model_box import ModelBox
from ...domain.testing import TestThresholds
from ... import config


class LiveTestingPanel(QtWidgets.QWidget):
    start_session_requested = QtCore.Signal()
    end_session_requested = QtCore.Signal()
    next_stage_requested = QtCore.Signal()
    previous_stage_requested = QtCore.Signal()
    package_model_requested = QtCore.Signal()
    activate_model_requested = QtCore.Signal(str)
    deactivate_model_requested = QtCore.Signal(str)
    # Discrete temperature testing actions
    discrete_new_requested = QtCore.Signal()
    discrete_add_requested = QtCore.Signal(str)
    discrete_test_selected = QtCore.Signal(str)

    def __init__(self, state: ViewState, controller: object = None, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.state = state
        self.controller = controller

        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(10)

        # Keep references to sub-boxes (logic lives in the boxes; panel remains public API facade)
        self._controls_box: SessionControlsBox
        self._guide_box: TestingGuideBox
        self._model_box: ModelBox

        # Build UI from small focused group boxes, then bind widgets onto this instance
        controls_box = SessionControlsBox(self)
        guide_box = TestingGuideBox(self)
        model_box = ModelBox(self)

        self._controls_box = controls_box
        self._guide_box = guide_box
        self._model_box = model_box

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

        # Guide
        self.guide_label = guide_box.guide_label

        # Session info/meta (now in controls_box)
        self.lbl_tester = controls_box.lbl_tester
        self.lbl_device = controls_box.lbl_device
        self.lbl_model = controls_box.lbl_model
        self.lbl_bw = controls_box.lbl_bw
        self.lbl_test_date_title = controls_box.lbl_test_date_title
        self.lbl_test_date = controls_box.lbl_test_date
        self.lbl_short_label_title = controls_box.lbl_short_label_title
        self.lbl_short_label = controls_box.lbl_short_label
        self.lbl_thresh_db = controls_box.lbl_thresh_db
        self.lbl_thresh_bw = controls_box.lbl_thresh_bw

        # Model panel
        self.lbl_current_model = model_box.lbl_current_model
        self.model_list = model_box.model_list
        self.lbl_model_status = model_box.lbl_model_status
        self.btn_activate = model_box.btn_activate
        self.btn_deactivate = model_box.btn_deactivate
        self.btn_package_model = model_box.btn_package_model

        # 5-column layout: [Session Controls] [Testing Guide] [empty] [empty] [Model]

        for w in (controls_box, guide_box):
            try:
                w.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
            except Exception:
                pass
            root.addWidget(w, 1)

        # Empty spacer columns 3 and 4
        root.addStretch(1)
        root.addStretch(1)

        # Model box in column 5
        try:
            model_box.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        except Exception:
            pass
        root.addWidget(model_box, 1)

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
        
        # Connect Controller Signals
        if self.controller:
            self.controller.view_session_started.connect(self._on_session_started)
            self.controller.view_session_ended.connect(self._on_session_ended)
            self.controller.view_stage_changed.connect(self._on_stage_changed)
            self.controller.view_grid_configured.connect(self.configure_grid)
            # Discrete temp test lists
            self.controller.discrete_tests_listed.connect(self.set_discrete_tests)

        # Discrete temp testing hooks
        try:
            self.session_mode_combo.currentTextChanged.connect(self._on_session_mode_changed)
            self.discrete_test_list.currentItemChanged.connect(self._on_discrete_test_changed)
            self.btn_discrete_new.clicked.connect(lambda: self.discrete_new_requested.emit())
            self.btn_discrete_add.clicked.connect(self._emit_discrete_add)
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
        # Reset add button enabled state whenever mode changes
        if not is_discrete:
            try:
                self.btn_discrete_add.setEnabled(False)
            except Exception:
                pass
        # Update capture default based on session type (Normal=OFF, Temperature=ON)
        try:
            is_temp_test = self.session_mode_combo.currentIndex() == 1  # "Temperature Test"
            self._controls_box.set_capture_default_for_mode(is_temp_test)
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
                self._controls_box.apply_discrete_test_meta(key)
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
        self._controls_box.apply_discrete_test_meta(key)

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
        self._controls_box.set_tester_name(tester or "")
        self.lbl_device.setText(device_id or "—")
        self.lbl_model.setText(model_id or "—")
        self._controls_box.set_body_weight_n(body_weight_n)

    def get_session_info(self) -> tuple[str, float]:
        """Get the current tester name and body weight from the editable fields."""
        return (
            self._controls_box.get_tester_name(),
            self._controls_box.get_body_weight_n()
        )

    def set_session_controls_locked(self, locked: bool) -> None:
        """Lock/unlock session info controls when a live test is active."""
        self._controls_box.set_session_active(locked)

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

    def set_stage_summary(self, stages: list[object] | None, *, grid_total_cells: int | None = None, current_stage_index: int | None = None) -> None:
        """Update the Testing Guide stage summary tracker."""
        try:
            self._guide_box.set_stage_summary(stages or [], grid_total_cells=grid_total_cells, current_stage_index=current_stage_index)
        except Exception:
            pass

    def set_next_stage_enabled(self, enabled: bool) -> None:
        try:
            self.btn_next.setEnabled(bool(enabled))
        except Exception:
            pass

    def set_prev_stage_enabled(self, enabled: bool) -> None:
        try:
            self.btn_prev.setEnabled(bool(enabled))
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

    def show_reconnect_hint(self) -> None:
        """Show the reconnect cable hint with fade-out."""
        self._model_box.show_reconnect_hint()

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
            # Show reconnect hint immediately when button is clicked
            self.show_reconnect_hint()
            self.activate_model_requested.emit(str(mid))

    def _emit_deactivate(self) -> None:
        mid = (self.lbl_current_model.text() or "").strip()
        if mid and mid != "—" and not mid.lower().startswith("loading"):
            self.set_model_status("Deactivating…")
            self.set_model_controls_enabled(False)
            # Show reconnect hint immediately when button is clicked
            self.show_reconnect_hint()
            self.deactivate_model_requested.emit(mid)

    def _on_start_clicked(self):
        if not self.controller:
            self.start_session_requested.emit()
            return

        # Prefer state for canonical selection (labels can show NN model ids, etc.)
        try:
            device_id = (self.state.selected_device_id or "").strip()
        except Exception:
            device_id = ""
        try:
            device_type = (self.state.selected_device_type or "").strip()  # plate type ("06", "07", etc.)
        except Exception:
            device_type = ""

        # Get the active NN model ID from the Model box (may differ from plate type)
        ml_model_id = (self.lbl_current_model.text() or "").strip()
        if ml_model_id in ("—", "No active model", "Loading...") or "loading" in ml_model_id.lower():
            ml_model_id = ""

        # Determine session mode
        is_temp_test = self.is_temperature_session()
        is_discrete_temp = self._is_discrete_temp_session()

        try:
            print(
                "[LiveTestingPanel] start_clicked "
                f"device_id={device_id or '∅'} plate_type={device_type or '∅'} "
                f"active_model={ml_model_id or '∅'} is_temp={is_temp_test} is_discrete={is_discrete_temp}"
            )
        except Exception:
            pass

        # Get session info directly from controls box (no popup dialog)
        tester, body_weight_n = self.get_session_info()
        capture_enabled = self._controls_box.is_capture_enabled()
        save_dir = self._controls_box.get_save_directory()

        # Save to state for next time
        if self.state:
            try:
                self.state.last_tester_name = tester
                self.state.last_body_weight_n = body_weight_n
            except Exception:
                pass

        # Compute thresholds based on plate type (not NN model)
        plate_type = device_type[:2] if device_type else "06"
        db_tol = float(config.THRESHOLDS_DB_N_BY_MODEL.get(plate_type, config.THRESHOLDS_DB_N_BY_MODEL[config.DEFAULT_DEVICE_TYPE]))
        bw_tol = float(config.get_passing_threshold("bw", plate_type, float(body_weight_n or 0.0)))
        thresholds = TestThresholds(dumbbell_tol_n=db_tol, bodyweight_tol_n=bw_tol)

        # Update UI with session metadata (show NN model if available, else plate type)
        display_model = ml_model_id or device_type
        self.set_metadata(tester, device_id, display_model, body_weight_n)
        self.set_thresholds(db_tol, bw_tol)

        session_config = {
            'tester': tester,
            'device_id': device_id,
            'model_id': device_type,  # plate type for grid dimensions
            'body_weight_n': body_weight_n,
            'thresholds': thresholds,
            'is_temp_test': is_temp_test,
            'is_discrete_temp': is_discrete_temp
        }
        try:
            print(f"[LiveTestingPanel] start_session: {session_config}")
        except Exception:
            pass
        self.controller.start_session(session_config)

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
        try:
            total = int(getattr(session, "grid_rows", 0) or 0) * int(getattr(session, "grid_cols", 0) or 0)
            self.set_stage_summary(getattr(session, "stages", []) or [], grid_total_cells=total if total > 0 else None, current_stage_index=0)
        except Exception:
            pass

    def _on_session_ended(self):
        self.btn_start.setEnabled(True)
        self.btn_end.setEnabled(False)
        self.btn_next.setEnabled(False)
        self.btn_prev.setEnabled(False)
        self.stage_label.setText("—")
        self.progress_label.setText("0 / 0 cells")
        try:
            self.set_stage_summary([], grid_total_cells=None)
        except Exception:
            pass

    def _on_stage_changed(self, index, stage):
        self.stage_label.setText(stage.name)
        # Update progress label if stage has info
        total = stage.total_cells
        done = 0
        try:
            for r in (getattr(stage, "results", {}) or {}).values():
                try:
                    if r is not None and getattr(r, "fz_mean_n", None) is not None:
                        done += 1
                except Exception:
                    continue
        except Exception:
            done = 0
        self.progress_label.setText(f"{int(done)} / {int(total)} cells")
        # Update guide
        self.set_stage_progress(stage.name, int(done), int(total))
        try:
            # Keep tracker fresh on stage changes too
            sess = getattr(getattr(self.controller, "service", None), "current_session", None) if self.controller else None
            if sess:
                total_cells = int(getattr(sess, "grid_rows", 0) or 0) * int(getattr(sess, "grid_cols", 0) or 0)
                self.set_stage_summary(getattr(sess, "stages", []) or [], grid_total_cells=total_cells if total_cells > 0 else None, current_stage_index=int(index))
        except Exception:
            pass
