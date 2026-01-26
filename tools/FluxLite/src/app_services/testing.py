from __future__ import annotations
import os
import logging
from typing import Optional, List, Dict, Tuple, Any

from PySide6 import QtCore

from .hardware import HardwareService
from .session_manager import SessionManager
from .repositories.test_file_repository import TestFileRepository
from .analysis.temperature_analyzer import TemperatureAnalyzer
from .discrete_temp_session_service import DiscreteTempSessionService
from .temperature_baseline_bias_service import TemperatureBaselineBiasService
from .temperature_coef_rollup_service import TemperatureCoefRollupService
from .temperature_processing_service import TemperatureProcessingService
from .temperature_coef_rollup.stage_split_per_test import export_stage_split_per_test_report
from ..domain.models import TestSession, TestResult, TestThresholds

logger = logging.getLogger(__name__)

class TestingService(QtCore.QObject):
    """
    Facade for testing operations.
    Delegates to specialized services for Session Management, Analysis, and Data Access.
    """
    session_started = QtCore.Signal(object)  # TestSession
    session_ended = QtCore.Signal(object)    # TestSession
    stage_changed = QtCore.Signal(int)       # new stage index
    cell_updated = QtCore.Signal(int, int, object)  # row, col, TestResult
    processing_status = QtCore.Signal(dict)  # {status, message, progress}

    def __init__(self, hardware_service: Optional[HardwareService] = None):
        super().__init__()
        self._hardware = hardware_service
        
        # Initialize sub-services
        self.repo = TestFileRepository()
        self.analyzer = TemperatureAnalyzer()
        self.session_manager = SessionManager()
        self._discrete = DiscreteTempSessionService()
        self._temp_processing = TemperatureProcessingService(repo=self.repo, hardware=self._hardware)
        self._temp_bias = TemperatureBaselineBiasService(
            repo=self.repo, analyzer=self.analyzer, processing=self._temp_processing
        )
        self._temp_rollup = TemperatureCoefRollupService(
            repo=self.repo, analyzer=self.analyzer, processing=self._temp_processing, bias=self._temp_bias
        )

        # Connect SessionManager signals to Facade signals
        self.session_manager.session_started.connect(self.session_started.emit)
        self.session_manager.session_ended.connect(self.session_ended.emit)
        self.session_manager.stage_changed.connect(self.stage_changed.emit)
        self.session_manager.cell_updated.connect(self.cell_updated.emit)

    # --- Session Management Delegates ---

    @property
    def current_session(self) -> Optional[TestSession]:
        return self.session_manager.current_session

    @property
    def active_cell(self) -> Optional[Tuple[int, int]]:
        return self.session_manager.active_cell

    @property
    def current_stage_index(self) -> int:
        return self.session_manager.current_stage_index

    def start_session(self, tester_name: str, device_id: str, model_id: str, body_weight_n: float, thresholds: TestThresholds | None, is_temp_test: bool = False, is_discrete_temp: bool = False) -> TestSession:
        return self.session_manager.start_session(
            tester_name, device_id, model_id, body_weight_n, thresholds, is_temp_test, is_discrete_temp
        )

    def end_session(self) -> None:
        if self.current_session and self.current_session.is_discrete_temp:
            self.write_discrete_session_csv()
        self.session_manager.end_session()

    def set_active_cell(self, row: int, col: int) -> None:
        self.session_manager.set_active_cell(row, col)

    def record_result(self, stage_idx: int, row: int, col: int, result: TestResult) -> None:
        self.session_manager.record_result(stage_idx, row, col, result)

    def next_stage(self) -> Optional[int]:
        return self.session_manager.next_stage()

    def prev_stage(self) -> Optional[int]:
        return self.session_manager.prev_stage()

    # --- Repository Delegates ---

    def list_temperature_tests(self, device_id: str) -> List[str]:
        return self.repo.list_temperature_tests(device_id)

    def list_temperature_devices(self) -> List[str]:
        return self.repo.list_temperature_devices()

    def list_discrete_tests(self) -> List[Tuple[str, str, str]]:
        return self.repo.list_discrete_tests()

    def get_temperature_test_details(self, csv_path: str) -> Dict[str, object]:
        return self.repo.get_temperature_test_details(csv_path)

    def analyze_discrete_temp_csv(self, csv_path: str) -> Tuple[bool, List[float]]:
        return self.repo.analyze_discrete_temp_csv(csv_path)

    # --- Analysis Delegates ---

    def analyze_temperature_processed_runs(
        self,
        baseline_csv: str,
        selected_csv: str,
        meta: Optional[Dict[str, object]] = None,
        baseline_data: Optional[Dict[str, object]] = None,
    ) -> Dict[str, object]:
        return self.analyzer.analyze_temperature_processed_runs(
            baseline_csv, selected_csv, meta, baseline_data
        )

    # --- Discrete Temperature Testing Logic ---

    def buffer_live_payload(self, payload: dict) -> None:
        """Buffer raw live payloads for discrete temperature analysis."""
        session = self.session_manager.current_session
        self._discrete.buffer_live_payload(session, payload)

    def accumulate_discrete_measurement(self, stage_name: str, window_start_ms: int, window_end_ms: int) -> bool:
        """
        Aggregate detailed sensor data over a stability window for discrete temp sessions.
        Returns True if successful.
        """
        session = self.session_manager.current_session
        return self._discrete.accumulate_discrete_measurement(session, stage_name, window_start_ms, window_end_ms)

    def write_discrete_session_csv(self) -> int:
        """Write the accumulated stats to the session CSV."""
        session = self.session_manager.current_session
        return self._discrete.write_discrete_session_csv(session)

    # --- Orchestration ---

    def run_temperature_processing(self, folder: str, device_id: str, csv_path: str, slopes: dict, room_temp_f: float = 72.0, mode: str = "legacy") -> None:
        self._temp_processing.run_temperature_processing(
            folder=folder,
            device_id=device_id,
            csv_path=csv_path,
            slopes=slopes,
            room_temp_f=room_temp_f,
            mode=mode,
            status_cb=self.processing_status.emit,
        )

    def compute_temperature_bias_for_device(
        self,
        device_id: str,
        *,
        min_temp_f: float | None = None,
        max_temp_f: float | None = None,
    ) -> Dict[str, object]:
        """
        Compute and store per-cell baseline bias for bias-controlled temperature grading.
        """
        return self._temp_bias.compute_and_store_bias_for_device(
            device_id=device_id,
            min_temp_f=min_temp_f,
            max_temp_f=max_temp_f,
            status_cb=self.processing_status.emit,
        )

    def run_temperature_coefs_across_plate_type(
        self,
        *,
        plate_type: str,
        coefs: dict,
        mode: str,
    ) -> Dict[str, object]:
        return self._temp_rollup.run_coefs_across_plate_type(
            plate_type=plate_type,
            coefs=coefs,
            mode=mode,
            status_cb=self.processing_status.emit,
        )

    def top3_temperature_coefs_for_plate_type(self, plate_type: str, *, sort_by: str = "mean_abs") -> List[Dict[str, object]]:
        return self._temp_rollup.top3_for_plate_type(plate_type, sort_by=str(sort_by or "mean_abs"))

    def reset_temperature_coef_rollup(self, plate_type: str, *, backup: bool = True) -> Dict[str, object]:
        """
        Clear the stored plate-type coefficient rollup (used for the "Top 3 Coef Combos" view).
        """
        return self._temp_rollup.reset_rollup(plate_type, backup=bool(backup))

    def aggregate_temperature_coefs_for_plate_type(
        self,
        plate_type: str,
        *,
        coefs: dict,
        mode: str,
    ) -> Optional[Dict[str, object]]:
        """
        Aggregate rollup metrics (selected/all mean_signed, etc.) for a specific coef set.
        Returns None if no eligible runs exist yet.
        """
        ck = self._temp_rollup.coef_key(mode, coefs)
        out = self._temp_rollup.aggregate_selected_all_mean_signed(plate_type, coef_key=ck)
        return dict(out or {}) if isinstance(out, dict) else None

    def list_existing_unified_temperature_coef_candidates_for_plate_type(
        self,
        plate_type: str,
        *,
        mode: str = "scalar",
        min_coef: float = 0.0,
        max_coef: float = 0.01,
    ) -> List[Dict[str, object]]:
        """
        Return existing unified candidates (x=y=z) found in the rollup for this plate type.
        Each row includes coef, coef_key, mean_signed, coverage.
        """
        return list(
            self._temp_rollup.list_existing_unified_candidates(
                plate_type,
                mode=mode,
                min_coef=min_coef,
                max_coef=max_coef,
            )
            or []
        )

    def export_distinct_temperature_experiment_report(
        self,
        plate_type: str,
        *,
        seed: dict,
        candidates: List[dict],
    ) -> Dict[str, object]:
        """Export distinct-coefs experiment CSVs for a plate type based on the current rollup."""
        return dict(self._temp_rollup.export_distinct_experiment_report(plate_type, seed=seed, candidates=candidates) or {})

    def export_stage_split_mae_per_test_report(
        self,
        plate_type: str,
        *,
        mode: str = "scalar",
        status_cb=None,
    ) -> Dict[str, object]:
        """Export a per-test CSV (excluding room-temp baselines) with best unified coef by MAE for BW and DB stages."""
        return dict(
            export_stage_split_per_test_report(
                repo=self.repo,
                analyzer=self.analyzer,
                processing=self._temp_processing,
                bias=self._temp_bias,
                plate_type=str(plate_type or ""),
                mode=str(mode or "scalar"),
                status_cb=status_cb,
            )
            or {}
        )

