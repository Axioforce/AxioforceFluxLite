from __future__ import annotations
import os
from PySide6 import QtCore
from ...services.testing import TestingService
from ...domain.models import TestResult
from ..presenters.grid_presenter import GridPresenter

class LiveTestController(QtCore.QObject):
    """
    Controller for the Live Testing UI.
    """
    # Signals for View
    view_session_started = QtCore.Signal(object)  # session
    view_session_ended = QtCore.Signal()
    view_stage_changed = QtCore.Signal(int, object)  # index, stage
    view_cell_updated = QtCore.Signal(int, int, object)  # row, col, view_model (dict or object)
    view_grid_configured = QtCore.Signal(int, int)
    # Discrete temp testing: available tests + analyzed temps for a selected test
    discrete_tests_listed = QtCore.Signal(list)  # list of (label, date, csv_path) - FILTERED
    discrete_filter_options = QtCore.Signal(list) # list of device_ids for filter combo
    discrete_temps_updated = QtCore.Signal(bool, object)  # includes_baseline, temps_f (list[float])

    def __init__(self, testing_service: TestingService):
        super().__init__()
        self.service = testing_service
        self.presenter = GridPresenter()
        
        # Filtering State
        self._all_discrete_tests = []
        self._current_type_filter = "All types"
        self._current_plate_filter = "All plates"
        
        # Forward service signals
        self.service.session_started.connect(self._on_session_started)
        self.service.session_ended.connect(self._on_session_ended)
        self.service.stage_changed.connect(self._on_stage_changed)
        self.service.cell_updated.connect(self._on_cell_updated)

    def start_session(self, config: dict):
        """
        Start a new test session.
        config: {
            'tester': str,
            'device_id': str,
            'model_id': str,
            'body_weight_n': float,
            'thresholds': TestThresholds,
            'is_temp_test': bool,
            'is_discrete_temp': bool
        }
        """
        self.service.start_session(
            tester_name=config.get('tester', ''),
            device_id=config.get('device_id', ''),
            model_id=config.get('model_id', ''),
            body_weight_n=config.get('body_weight_n', 0.0),
            thresholds=config.get('thresholds'),
            is_temp_test=config.get('is_temp_test', False),
            is_discrete_temp=config.get('is_discrete_temp', False)
        )

    def end_session(self):
        self.service.end_session()

    def next_stage(self):
        self.service.next_stage()

    def prev_stage(self):
        self.service.prev_stage()

    def refresh_discrete_tests(self):
        """Fetch available discrete temperature tests and update view."""
        self._all_discrete_tests = self.service.list_discrete_tests()
        
        # 1. Compute available device IDs for the filter dropdown
        device_ids: set[str] = set()
        base_dir = "discrete_temp_testing"
        for _label, _date_str, key in self._all_discrete_tests:
            path = str(key)
            try:
                rel = os.path.relpath(path, base_dir)
            except Exception:
                rel = path
            parts = rel.split(os.sep)
            if parts and parts[0]:
                device_ids.add(parts[0])
        
        self.discrete_filter_options.emit(sorted(list(device_ids)))
        
        # 2. Emit filtered list
        self._emit_filtered_list()

    def set_filters(self, type_filter: str, plate_filter: str):
        self._current_type_filter = str(type_filter or "All types")
        self._current_plate_filter = str(plate_filter or "All plates")
        self._emit_filtered_list()

    def _emit_filtered_list(self):
        """Apply filters and emit the list."""
        filtered = []
        base_dir = "discrete_temp_testing"
        
        type_sel = self._current_type_filter
        plate_sel = self._current_plate_filter

        for label, date_str, key in self._all_discrete_tests:
            path = str(key)
            # Derive device id and type from path
            try:
                rel = os.path.relpath(path, base_dir)
            except Exception:
                rel = path
            parts = rel.split(os.sep)
            device_id = parts[0] if parts else ""
            dev_type = ""
            if device_id:
                if "." in device_id:
                    dev_type = device_id.split(".", 1)[0]
                else:
                    dev_type = device_id[:2]

            # Apply filters
            if type_sel != "All types" and dev_type != type_sel:
                continue
            if plate_sel != "All plates" and device_id != plate_sel:
                continue
            
            # Pass tuple + device_id for convenience if needed, but signature matches original list
            # (label, date, key)
            filtered.append((label, date_str, key))
            
        self.discrete_tests_listed.emit(filtered)

    @QtCore.Slot(str)
    def on_discrete_test_selected(self, csv_path: str) -> None:
        """
        Handle selection changes from the Discrete Temp test picker.

        Computes baseline presence + temperature list from the selected
        discrete_temp_session CSV and pushes it to the view.
        """
        if not csv_path:
            self.discrete_temps_updated.emit(False, [])
            return
        includes_baseline, temps_f = self.service.analyze_discrete_temp_csv(csv_path)
        self.discrete_temps_updated.emit(bool(includes_baseline), list(temps_f or []))

    def handle_cell_click(self, row: int, col: int, current_data: dict | None = None) -> None:
        """
        Handle a user clicking a cell in the live-testing grid.

        For now we treat this as a simple "mark cell as measured" action:
        - Use the currently active stage from the TestingService.
        - Create a lightweight TestResult with whatever telemetry the caller
          provides (if any).
        - Delegate persistence/broadcast to TestingService.record_result.

        This keeps the controller responsible for session state, while callers
        remain free to evolve the shape of current_data over time.
        """
        # No active session -> ignore the click
        if not self.service.current_session:
            return

        try:
            stage_idx = int(getattr(self.service, "current_stage_index", 0))
        except Exception:
            stage_idx = 0

        # For discrete temp, try to capture data window (last 1 second)
        session = self.service.current_session
        if session and session.is_discrete_temp:
            import time
            now_ms = int(time.time() * 1000)
            try:
                stage = session.stages[stage_idx]
                stage_name = stage.name
            except Exception:
                stage_name = "Unknown"
                
            # Assume 1 second stability window before click
            success = self.service.accumulate_discrete_measurement(stage_name, now_ms - 1000, now_ms)
            if not success:
                print("Warning: Failed to capture discrete data window")
                # TODO: Warn user via UI signal?
                # For now, we proceed to record the result visually, but the data row might be missing.
                # Actually, if accumulation fails, we probably shouldn't mark the cell as done.
                return

        payload = current_data or {}
        # Best-effort extraction of basic telemetry; callers are free to omit.
        try:
            fz_mean = payload.get("fz_mean_n")
        except Exception:
            fz_mean = None
        try:
            cop_x = payload.get("cop_x_mm")
            cop_y = payload.get("cop_y_mm")
        except Exception:
            cop_x = cop_y = None

        result = TestResult(
            row=row,
            col=col,
            fz_mean_n=fz_mean,
            cop_x_mm=cop_x,
            cop_y_mm=cop_y,
        )

        # Let the service own mutation + signal emission
        self.service.set_active_cell(row, col)
        self.service.record_result(stage_idx, row, col, result)

    def _on_session_started(self, session):
        self.view_grid_configured.emit(session.grid_rows, session.grid_cols)
        self.view_session_started.emit(session)
        # Emit initial stage
        if session.stages:
            self.view_stage_changed.emit(0, session.stages[0])

    def _on_session_ended(self, session):
        self.view_session_ended.emit()

    def _on_stage_changed(self, index):
        if self.service.current_session and 0 <= index < len(self.service.current_session.stages):
            self.view_stage_changed.emit(index, self.service.current_session.stages[index])

    def _on_cell_updated(self, row, col, result):
        # Calculate color/display using Presenter
        session = self.service.current_session
        if session:
            idx = self.service.current_stage_index
            if 0 <= idx < len(session.stages):
                stage = session.stages[idx]
                target_n = stage.target_n
                # Tolerance? It's not in stage directly?
                # TestingService uses thresholds from session.thresholds for determining PASS/FAIL?
                # Or it is in stage?
                # TestStage in domain/testing.py has `target_n`, but no tolerance?
                # Wait, TestStage definition I used:
                # @dataclass class TestStage: ... target_n: float ...
                # It doesn't have tolerance.
                # Thresholds are in session.thresholds.
                
                threshold_n = 10.0 # fallback
                if stage.name == "45 lb DB": # rough check or use location?
                    threshold_n = session.thresholds.dumbbell_tol_n
                elif "Body Weight" in stage.name:
                    threshold_n = session.thresholds.bodyweight_tol_n
                
                vm = self.presenter.compute_live_cell(result, target_n, threshold_n)
                
                # Convert to simple object/dict for signal
                payload = {
                    "text": vm.text,
                    "color": vm.color,
                    "tooltip": vm.tooltip,
                    # include raw result data if needed?
                    "fz_mean_n": result.fz_mean_n
                }
                self.view_cell_updated.emit(row, col, payload)
                return

        # Fallback if no session/stage context
        self.view_cell_updated.emit(row, col, result)
