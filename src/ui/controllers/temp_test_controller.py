from __future__ import annotations
from PySide6 import QtCore
from typing import Optional, List, Dict, Tuple
import os

from ... import config
from ...app_services.testing import TestingService
from ...app_services.hardware import HardwareService
from ..presenters.grid_presenter import GridPresenter
from .temp_test_controller_actions import TempTestControllerActionsMixin
from .temp_test_workers import (
    BiasComputeWorker,
    PlateTypeAutoSearchWorker,
    PlateTypeRollupWorker,
    ProcessingWorker,
    TemperatureAnalysisWorker,
    TemperatureImportWorker,
    TemperatureAutoUpdateWorker,
)

class TempTestController(TempTestControllerActionsMixin, QtCore.QObject):
    """
    Controller for the Temperature Testing UI.
    Manages test file listing, processing, and configuration.
    """
    # Signals for View
    tests_listed = QtCore.Signal(list)  # list of file paths
    devices_listed = QtCore.Signal(list)  # list of device IDs
    processing_status = QtCore.Signal(dict)  # forwarded from service
    processed_runs_loaded = QtCore.Signal(list)
    stages_loaded = QtCore.Signal(list)
    test_meta_loaded = QtCore.Signal(dict)
    analysis_ready = QtCore.Signal(dict)
    analysis_status = QtCore.Signal(dict)
    # Grid display data: dict with keys 'grid_info', 'baseline_cells', 'selected_cells'
    grid_display_ready = QtCore.Signal(dict)
    # Plot request: dict with baseline_path, selected_path, body_weight_n
    plot_ready = QtCore.Signal(dict)
    # Bias status: {available: bool, message: str}
    bias_status = QtCore.Signal(dict)
    # Plate-type batch status (big picture)
    rollup_ready = QtCore.Signal(dict)
    # Auto-search status (big picture)
    auto_search_status = QtCore.Signal(dict)
    # Import status
    import_ready = QtCore.Signal(dict)
    # Auto-update status (post-import)
    auto_update_status = QtCore.Signal(dict)
    auto_update_done = QtCore.Signal(dict)

    def __init__(self, testing_service: TestingService, hardware_service: HardwareService):
        super().__init__()
        self.testing = testing_service
        self.hardware = hardware_service
        self.presenter = GridPresenter()
        
        self._current_meta: Dict[str, object] = {}
        self._current_processed_runs: List[Dict[str, object]] = []
        self._current_test_csv: Optional[str] = None
        self._analysis_worker: Optional[TemperatureAnalysisWorker] = None
        self._pending_analysis: Optional[tuple[str, str, Dict[str, object]]] = None
        self._current_selected_path: Optional[str] = None
        self._current_baseline_path: Optional[str] = None
        
        # Cache for baseline analysis
        self._cached_baseline_path: Optional[str] = None
        self._cached_baseline_result: Optional[Dict[str, object]] = None
        self._last_analysis_payload: Optional[Dict[str, object]] = None
        self._bias_cache: Optional[Dict[str, object]] = None
        self._grading_mode: str = "absolute"  # "absolute" | "bias"
        self._pending_bias_recompute: bool = False
        self._last_processing_device_id: Optional[str] = None
        self._bias_worker: Optional[BiasComputeWorker] = None
        self._rollup_worker: Optional[QtCore.QThread] = None
        self._auto_search_worker: Optional[QtCore.QThread] = None
        self._import_worker: Optional[QtCore.QThread] = None
        self._auto_update_worker: Optional[QtCore.QThread] = None

        # Clear any retained state
        self._last_analysis_payload = None

        # Forward service signals
        self.testing.processing_status.connect(self.processing_status.emit)
        # When processing completes, reload current test details so new processed runs appear.
        # This does NOT auto-select a run (analysis still requires explicit selection).
        self.testing.processing_status.connect(self._on_processing_status)
        
        self._worker = None # Keep reference to prevent GC

    def import_temperature_tests(self, file_paths: list[str]) -> None:
        """
        Import raw temperature tests (CSV + meta.json) into temp_testing/<device_id>/.
        """
        if self._import_worker and self._import_worker.isRunning():
            self.processing_status.emit({"status": "error", "message": "Import already in progress"})
            return

        worker = TemperatureImportWorker(list(file_paths or []))
        self._import_worker = worker
        worker.finished.connect(lambda: setattr(self, "_import_worker", None))
        worker.result_ready.connect(self.import_ready.emit)
        worker.start()

    def run_auto_update_metrics(self, plate_types: list[str], _device_ids: list[str] | None = None) -> None:
        """
        Post-import automation:
          - resets rollups for affected plate types
          - runs unified auto-search per plate type
        """
        if self._auto_update_worker and self._auto_update_worker.isRunning():
            self.processing_status.emit({"status": "error", "message": "Auto-update already running"})
            return
        if self._rollup_worker and self._rollup_worker.isRunning():
            self.processing_status.emit({"status": "error", "message": "Batch rollup already running"})
            return
        if self._auto_search_worker and self._auto_search_worker.isRunning():
            self.processing_status.emit({"status": "error", "message": "Auto search already running"})
            return

        worker = TemperatureAutoUpdateWorker(self.testing, list(plate_types or []))
        self._auto_update_worker = worker
        worker.finished.connect(lambda: setattr(self, "_auto_update_worker", None))
        worker.status_ready.connect(self.auto_update_status.emit)
        worker.result_ready.connect(self.auto_update_done.emit)
        worker.start()

    def refresh_tests(self, device_id: str):
        """List available tests for the device."""
        tests = self.testing.list_temperature_tests(device_id)
        self.tests_listed.emit(tests)

    def refresh_devices(self):
        """List available devices in temp_testing folder."""
        devices = self.testing.list_temperature_devices()
        self.devices_listed.emit(devices)

    def run_processing(self, payload: dict):
        """
        Run temperature processing on a test file.
        """
        device_id = payload.get("device_id")
        csv_path = payload.get("csv_path")
        slopes = payload.get("slopes", {})
        room_temp_f = float(payload.get("room_temperature_f", config.TEMP_IDEAL_ROOM_TEMP_F))
        mode = str(payload.get("mode", "scalar"))
        
        if not device_id or not csv_path:
            self.processing_status.emit({"status": "error", "message": "Please select a device and a test file."})
            return
        self._pending_bias_recompute = True
        self._last_processing_device_id = str(device_id)
            
        import os
        folder = payload.get("folder") or os.path.dirname(csv_path)
        
        # Run in background
        if self._worker and self._worker.isRunning():
            self.processing_status.emit({"status": "error", "message": "Processing already in progress"})
            return

        self._worker = ProcessingWorker(self.testing, folder, device_id, csv_path, slopes, room_temp_f, mode)
        # Clean up worker reference when done
        self._worker.finished.connect(lambda: setattr(self, '_worker', None))
        self._worker.start()

    def load_test_details(self, csv_path: str) -> None:
        """Load metadata for a selected test CSV."""
        if not csv_path:
            self.processed_runs_loaded.emit([])
            self.stages_loaded.emit(["All"])
            self.test_meta_loaded.emit({})
            self._current_meta = {}
            self._current_processed_runs = []
            self._current_test_csv = None
            return
            
        # Invalidate baseline cache when switching tests
        if self._current_test_csv != csv_path:
            self._cached_baseline_path = None
            self._cached_baseline_result = None
            
        try:
            details = self.testing.get_temperature_test_details(csv_path)
        except Exception as exc:
            self.processing_status.emit({"status": "error", "message": str(exc)})
            return
        self._current_meta = dict(details.get("meta", {}) or {})
        self._current_processed_runs = list(details.get("processed_runs", []) or [])
        self._current_test_csv = csv_path
        self.processed_runs_loaded.emit(details.get("processed_runs", []))
        # Use fixed stage names that match analysis stage keys
        # "All" shows combined, "45 lb DB" -> "db", "Body Weight" -> "bw"
        stage_names = ["All", "45 lb DB", "Body Weight"]
        self.stages_loaded.emit(stage_names)
        self.test_meta_loaded.emit(details.get("meta", {}))

        # Refresh bias cache for this device (enables/disables bias-controlled toggle).
        try:
            device_id = str(self._current_meta.get("device_id") or "").strip()
            if not device_id:
                device_id = os.path.basename(os.path.dirname(str(csv_path or ""))).strip()
            self._refresh_bias_cache(device_id)
        except Exception:
            self._bias_cache = None
            self.bias_status.emit({"available": False, "message": ""})

        # Best-effort: refresh big-picture top3 for current plate type.
        try:
            self.rollup_ready.emit({"ok": True, "message": ""})
        except Exception:
            pass

    def select_processed_run(self, entry: dict) -> None:
        path = str((entry or {}).get("path") or "").strip()
        if not path:
            return
        baseline_path = ""
        for run in self._current_processed_runs:
            if run.get("is_baseline"):
                baseline_path = str(run.get("path") or "").strip()
                break
        if not baseline_path:
            self.processing_status.emit({"status": "error", "message": "Baseline CSV missing for this test"})
            return
        
        # Track current paths for plotting
        self._current_baseline_path = baseline_path
        self._current_selected_path = path
        
        meta = dict(self._current_meta or {})
        self._queue_analysis(baseline_path, path, meta)

    def _queue_analysis(self, baseline_csv: str, selected_csv: str, meta: Dict[str, object]) -> None:
        if self._analysis_worker and self._analysis_worker.isRunning():
            self._pending_analysis = (baseline_csv, selected_csv, meta)
            return
        
        # Check cache for baseline
        baseline_data = None
        if self._cached_baseline_path == baseline_csv and self._cached_baseline_result:
            baseline_data = self._cached_baseline_result
            
        worker = TemperatureAnalysisWorker(self.testing, baseline_csv, selected_csv, meta, baseline_data=baseline_data)
        self._analysis_worker = worker
        self.analysis_status.emit({"status": "running", "message": "Analyzing processed run..."})
        worker.result_ready.connect(self._on_analysis_result)
        worker.error.connect(self._on_analysis_error)
        worker.finished.connect(self._on_analysis_worker_finished)
        worker.start()

    def _on_analysis_worker_finished(self) -> None:
        self._analysis_worker = None
        if self._pending_analysis:
            baseline_csv, selected_csv, meta = self._pending_analysis
            self._pending_analysis = None
            self._queue_analysis(baseline_csv, selected_csv, meta)

    def _on_processing_status(self, payload: dict) -> None:
        status = str((payload or {}).get("status") or "").lower()
        if status != "completed":
            return
        if not self._current_test_csv:
            return
        QtCore.QTimer.singleShot(0, lambda: self.load_test_details(self._current_test_csv))

        # After the user presses Process, recompute per-device room-temp bias.
        if self._pending_bias_recompute and self._last_processing_device_id:
            self._pending_bias_recompute = False
            self._start_bias_compute(self._last_processing_device_id)

    def _start_bias_compute(self, device_id: str) -> None:
        if self._bias_worker and self._bias_worker.isRunning():
            return
        dev = str(device_id or "").strip()
        if not dev:
            return
        self._bias_worker = BiasComputeWorker(self.testing, dev)
        self._bias_worker.finished.connect(lambda: setattr(self, "_bias_worker", None))
        self._bias_worker.result_ready.connect(self._on_bias_compute_result)
        self._bias_worker.start()

    def _on_bias_compute_result(self, result: dict) -> None:
        ok = bool((result or {}).get("ok"))
        if ok:
            # Reload cache from disk for consistency.
            try:
                self._refresh_bias_cache(str((result.get("payload") or {}).get("device_id") or self._last_processing_device_id or ""))
            except Exception:
                self._bias_cache = None
                self.bias_status.emit({"available": False, "message": ""})
                return
            self.bias_status.emit({"available": True, "message": ""})
            return

        # Failure: disable bias-controlled mode and provide details.
        errs = list((result or {}).get("errors") or [])
        msg = str((result or {}).get("message") or "Bias-controlled grading disabled.")
        details = "\n".join([msg] + [f"- {e}" for e in errs if e])
        self._bias_cache = None
        self.bias_status.emit({"available": False, "message": details})

    def _refresh_bias_cache(self, device_id: str) -> None:
        dev = str(device_id or "").strip()
        if not dev:
            self._bias_cache = None
            self.bias_status.emit({"available": False, "message": ""})
            return
        data = None
        try:
            data = self.testing.repo.load_temperature_bias_cache(dev)
        except Exception:
            data = None
        if not isinstance(data, dict):
            self._bias_cache = None
            self.bias_status.emit({"available": False, "message": ""})
            return
        bias = data.get("bias")
        rows = data.get("rows")
        cols = data.get("cols")
        if not isinstance(bias, list) or not isinstance(rows, int) or not isinstance(cols, int):
            self._bias_cache = None
            self.bias_status.emit({"available": False, "message": ""})
            return
        if len(bias) != rows or any((not isinstance(r, list) or len(r) != cols) for r in bias):
            self._bias_cache = None
            self.bias_status.emit({"available": False, "message": ""})
            return
        self._bias_cache = data
        self.bias_status.emit({"available": True, "message": ""})

    def set_grading_mode(self, mode: str) -> None:
        mode_lc = str(mode or "").strip().lower()
        self._grading_mode = "bias" if mode_lc.startswith("bias") else "absolute"

    def grading_mode(self) -> str:
        return self._grading_mode

    def bias_map(self) -> Optional[list]:
        if not isinstance(self._bias_cache, dict):
            return None
        bias = self._bias_cache.get("bias_all")
        if isinstance(bias, list):
            return bias
        legacy = self._bias_cache.get("bias")
        return legacy if isinstance(legacy, list) else None

    def bias_map_db(self) -> Optional[list]:
        if not isinstance(self._bias_cache, dict):
            return None
        bias = self._bias_cache.get("bias_db")
        return bias if isinstance(bias, list) else None

    def bias_map_bw(self) -> Optional[list]:
        if not isinstance(self._bias_cache, dict):
            return None
        bias = self._bias_cache.get("bias_bw")
        return bias if isinstance(bias, list) else None

    def bias_cache(self) -> Optional[dict]:
        return dict(self._bias_cache or {}) if isinstance(self._bias_cache, dict) else None

    def current_plate_type(self) -> str:
        """
        Plate type derived from device_id (e.g., '06' from '06.00000025').
        """
        dev = str((self._current_meta or {}).get("device_id") or "").strip()
        if dev:
            return dev.split(".", 1)[0].strip()
        return ""

    @staticmethod
    def plate_type_from_device_id(device_id: str) -> str:
        dev = str(device_id or "").strip()
        if dev:
            return dev.split(".", 1)[0].strip()
        return ""

    def top3_for_plate_type(self, plate_type: str) -> dict:
        """
        Return both top-3 rankings:
          - by mean abs % (default)
          - by abs(mean signed %)
        """
        pt = str(plate_type or "").strip()
        if not pt:
            return {"mean_abs": [], "signed_abs": []}
        try:
            rows_abs = self.testing.top3_temperature_coefs_for_plate_type(pt, sort_by="mean_abs")
        except Exception:
            rows_abs = []
        try:
            rows_signed = self.testing.top3_temperature_coefs_for_plate_type(pt, sort_by="signed_abs")
        except Exception:
            rows_signed = []
        return {"mean_abs": list(rows_abs or []), "signed_abs": list(rows_signed or [])}

    def top3_for_current_plate_type(self) -> list[dict]:
        pt = self.current_plate_type()
        if not pt:
            return []
        try:
            rows = self.testing.top3_temperature_coefs_for_plate_type(pt, sort_by="mean_abs")
            return list(rows or [])
        except Exception:
            return []

    def load_bias_cache_for_device(self, device_id: str) -> bool:
        """
        Load bias cache from disk (if present) and update bias_status.
        Returns True if a valid cache was loaded.
        """
        try:
            self._refresh_bias_cache(str(device_id or ""))
        except Exception:
            return False
        return isinstance(self._bias_cache, dict)

    def compute_bias_for_device(self, device_id: str) -> None:
        """
        Start background bias compute for the given device (room-temp baseline bias).
        """
        self._start_bias_compute(str(device_id or ""))

    def run_coefs_across_plate_type(self, coefs: dict, mode: str) -> None:
        """
        Batch-run a coefficient set across all devices/tests for the current plate type.
        """
        plate_type = self.current_plate_type()
        if not plate_type:
            self.processing_status.emit({"status": "error", "message": "Missing plate type (no device selected)."})
            return

        if self._rollup_worker and self._rollup_worker.isRunning():
            self.processing_status.emit({"status": "error", "message": "Batch rollup already running"})
            return

        worker = PlateTypeRollupWorker(self.testing, plate_type, coefs, mode)
        self._rollup_worker = worker
        worker.finished.connect(lambda: setattr(self, "_rollup_worker", None))
        worker.result_ready.connect(self.rollup_ready.emit)
        worker.start()

    def run_auto_search_for_current_plate_type(self, *, search_mode: str = "unified", mode: str = "scalar") -> None:
        """
        Run an automated coefficient search for the current plate type.
        Currently supports only search_mode == 'unified' (x=y=z).
        """
        plate_type = self.current_plate_type()
        if not plate_type:
            self.processing_status.emit({"status": "error", "message": "Missing plate type (no device selected)."})
            return

        if self._rollup_worker and self._rollup_worker.isRunning():
            self.processing_status.emit({"status": "error", "message": "Batch rollup already running"})
            return
        if self._auto_search_worker and self._auto_search_worker.isRunning():
            self.processing_status.emit({"status": "error", "message": "Auto search already running"})
            return

        sm = str(search_mode or "unified").strip().lower()
        if sm not in ("unified",):
            self.processing_status.emit({"status": "error", "message": f"Unsupported auto search mode: {search_mode}"})
            return

        worker = PlateTypeAutoSearchWorker(self.testing, plate_type, mode, sm)
        self._auto_search_worker = worker
        worker.finished.connect(lambda: setattr(self, "_auto_search_worker", None))
        worker.status_ready.connect(self.auto_search_status.emit)

        def _on_done(payload: dict) -> None:
            payload = payload or {}
            ok = bool(payload.get("ok"))
            msg = str(payload.get("message") or "")
            if msg:
                try:
                    self.auto_search_status.emit({"status": "completed" if ok else "error", "message": msg})
                except Exception:
                    pass
            # Reuse rollup_ready so the panel refreshes top3 consistently.
            errs = []
            if not ok and msg:
                errs = [msg]
            try:
                out = {"ok": ok, "message": msg, "errors": errs, "best": payload.get("best")}
                self.rollup_ready.emit(out)
            except Exception:
                pass

        worker.result_ready.connect(_on_done)
        worker.start()

    def reset_rollup_for_current_plate_type(self, *, backup: bool = True) -> None:
        """
        Clear the plate-type coefficient rollup (this resets the Big Picture Top-3 display).
        """
        plate_type = self.current_plate_type()
        if not plate_type:
            self.processing_status.emit({"status": "error", "message": "Missing plate type (no device selected)."})
            return
        try:
            res = self.testing.reset_temperature_coef_rollup(plate_type, backup=bool(backup))
        except Exception as exc:
            res = {"ok": False, "message": str(exc), "errors": [str(exc)]}
        # Reuse rollup_ready channel so the panel refreshes top3 consistently.
        try:
            self.rollup_ready.emit(dict(res or {}))
        except Exception:
            pass

