from __future__ import annotations
from PySide6 import QtCore
from ...services.testing import TestingService
from ...domain.models import TestResult

class LiveTestController(QtCore.QObject):
    """
    Controller for the Live Testing UI.
    """
    # Signals for View
    view_session_started = QtCore.Signal(object) # session
    view_session_ended = QtCore.Signal()
    view_stage_changed = QtCore.Signal(int, object) # index, stage
    view_cell_updated = QtCore.Signal(int, int, object) # row, col, result
    view_grid_configured = QtCore.Signal(int, int)

    def __init__(self, testing_service: TestingService):
        super().__init__()
        self.service = testing_service
        
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

    def handle_cell_click(self, row: int, col: int, current_data: dict):
        # Logic to record result based on current data (e.g. from telemetry)
        # This requires the controller to know the current telemetry or for it to be passed in.
        # For now, we assume the view passes the result to be recorded, 
        # OR the controller should be listening to telemetry to capture the snapshot.
        
        # In the original code, the panel grabbed the current snapshot from the bridge/state.
        # Ideally, the controller should have access to the latest telemetry.
        pass

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
        self.view_cell_updated.emit(row, col, result)
