from __future__ import annotations
from typing import Optional, Tuple, List
from PySide6 import QtCore

from .. import config
from ..domain.testing import TestSession, TestStage, TestResult, TestThresholds
from .geometry import GeometryService

class SessionManager(QtCore.QObject):
    """
    Manages the lifecycle and state of a live test session.
    """
    session_started = QtCore.Signal(object)  # TestSession
    session_ended = QtCore.Signal(object)    # TestSession
    stage_changed = QtCore.Signal(int)       # new stage index
    cell_updated = QtCore.Signal(int, int, object)  # row, col, TestResult

    def __init__(self):
        super().__init__()
        self._current_session: Optional[TestSession] = None
        self._active_cell: Optional[Tuple[int, int]] = None
        self._current_stage_index: int = 0

    @property
    def current_session(self) -> Optional[TestSession]:
        return self._current_session

    @property
    def active_cell(self) -> Optional[Tuple[int, int]]:
        return self._active_cell

    @property
    def current_stage_index(self) -> int:
        """Return the index of the currently active stage in the session."""
        return int(self._current_stage_index)

    def start_session(
        self,
        tester_name: str,
        device_id: str,
        model_id: str,
        body_weight_n: float,
        thresholds: TestThresholds | None,
        is_temp_test: bool = False,
        is_discrete_temp: bool = False
    ) -> TestSession:
        rows, cols = GeometryService.get_grid_dimensions(model_id)

        # Create default thresholds if not provided
        if thresholds is None:
            device_type = (model_id or "06")[:2]
            db_tol = float(config.THRESHOLDS_DB_N_BY_MODEL.get(device_type, config.THRESHOLDS_DB_N_BY_MODEL[config.DEFAULT_DEVICE_TYPE]))
            bw_tol = float(config.get_passing_threshold("bw", device_type, float(body_weight_n or 0.0)))
            thresholds = TestThresholds(dumbbell_tol_n=float(db_tol), bodyweight_tol_n=float(bw_tol))
        
        session = TestSession(
            tester_name=tester_name,
            device_id=device_id,
            model_id=model_id,
            body_weight_n=body_weight_n,
            thresholds=thresholds,
            grid_rows=rows,
            grid_cols=cols,
            is_temp_test=is_temp_test,
            is_discrete_temp=is_discrete_temp
        )
        
        # Initialize stages (default logic)
        stages: List[TestStage] = []
        total_cells = int(rows * cols)

        # Normal live testing: 6 stages (A: DB / Two leg BW / One leg BW, then repeat at B)
        if not bool(is_temp_test) and not bool(is_discrete_temp):
            idx = 0
            for loc in ("A", "B"):
                stages.append(TestStage(idx, "45 lb", loc, float(config.TEMP_DB_TARGET_N), total_cells))
                idx += 1
                stages.append(TestStage(idx, "Two Leg", loc, float(body_weight_n), total_cells))
                idx += 1
                stages.append(TestStage(idx, "One Leg", loc, float(body_weight_n), total_cells))
                idx += 1
        else:
            # Temperature/discrete temp modes: 2 stages at Location A only
            stages.append(TestStage(0, "45 lb", "A", float(config.TEMP_DB_TARGET_N), total_cells))
            stages.append(TestStage(1, "Bodyweight", "A", float(body_weight_n), total_cells))
        
        session.stages = stages
        session.start()
        
        self._current_session = session
        self._current_stage_index = 0
        self.session_started.emit(session)
        return session

    def end_session(self) -> None:
        if self._current_session:
            self._current_session.end()
            self.session_ended.emit(self._current_session)
            self._current_session = None
            self._active_cell = None

    def set_active_cell(self, row: int, col: int) -> None:
        self._active_cell = (row, col)

    def record_result(self, stage_idx: int, row: int, col: int, result: TestResult) -> None:
        if not self._current_session:
            return
            
        if 0 <= stage_idx < len(self._current_session.stages):
            stage = self._current_session.stages[stage_idx]
            stage.results[(row, col)] = result
            self.cell_updated.emit(row, col, result)

    def next_stage(self) -> Optional[int]:
        if not self._current_session or not self._current_session.stages:
            return None

        if self._current_stage_index < len(self._current_session.stages) - 1:
            self._current_stage_index += 1
            self.stage_changed.emit(self._current_stage_index)
            return self._current_stage_index
        return None

    def prev_stage(self) -> Optional[int]:
        if not self._current_session or not self._current_session.stages:
            return None
            
        if self._current_stage_index > 0:
            self._current_stage_index -= 1
            self.stage_changed.emit(self._current_stage_index)
            return self._current_stage_index
        return None

