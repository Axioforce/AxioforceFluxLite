from __future__ import annotations
from PySide6 import QtCore
from typing import Optional, List, Dict, Tuple
import os

from ... import config
from ...services.testing import TestingService
from ...services.hardware import HardwareService

class ProcessingWorker(QtCore.QThread):
    """Worker thread for running temperature processing in the background."""
    def __init__(self, service: TestingService, folder: str, device_id: str, csv_path: str, slopes: dict, room_temp_f: float):
        super().__init__()
        self.service = service
        self.folder = folder
        self.device_id = device_id
        self.csv_path = csv_path
        self.slopes = slopes
        self.room_temp_f = float(room_temp_f)

    def run(self):
        self.service.run_temperature_processing(self.folder, self.device_id, self.csv_path, self.slopes, self.room_temp_f)

class TemperatureAnalysisWorker(QtCore.QThread):
    """Background worker for processed run analysis."""

    result_ready = QtCore.Signal(dict)
    error = QtCore.Signal(str)

    def __init__(self, service: TestingService, baseline_csv: str, selected_csv: str, meta: Dict[str, object], baseline_data: Optional[Dict[str, object]] = None):
        super().__init__()
        self.service = service
        self.baseline_csv = baseline_csv
        self.selected_csv = selected_csv
        self.meta = dict(meta or {})
        self.baseline_data = baseline_data

    def run(self) -> None:
        try:
            payload = self.service.analyze_temperature_processed_runs(
                self.baseline_csv,
                self.selected_csv,
                self.meta,
                baseline_data=self.baseline_data,
            )
            self.result_ready.emit(payload)
        except Exception as exc:
            self.error.emit(str(exc))

class TempTestController(QtCore.QObject):
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
    # Each cell is: {'row': int, 'col': int, 'color_bin': str, 'text': str}
    grid_display_ready = QtCore.Signal(dict)
    # Plot request: dict with baseline_path, selected_path, body_weight_n
    plot_ready = QtCore.Signal(dict)

    def __init__(self, testing_service: TestingService, hardware_service: HardwareService):
        super().__init__()
        self.testing = testing_service
        self.hardware = hardware_service
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

        # Forward service signals
        self.testing.processing_status.connect(self.processing_status.emit)
        self.testing.processing_status.connect(self._on_processing_status)
        
        self._worker = None # Keep reference to prevent GC

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
        payload: {
            'device_id': str,
            'csv_path': str,
            'slopes': dict,
            'folder': str (optional, default to parent of csv_path),
            'room_temperature_f': float (optional, default 72.0)
        }
        """
        device_id = payload.get("device_id")
        csv_path = payload.get("csv_path")
        slopes = payload.get("slopes", {})
        room_temp_f = float(payload.get("room_temperature_f", 72.0))
        
        if not device_id or not csv_path:
            return
            
        import os
        folder = payload.get("folder") or os.path.dirname(csv_path)
        
        # Run in background
        if self._worker and self._worker.isRunning():
            self.processing_status.emit({"status": "error", "message": "Processing already in progress"})
            return

        self._worker = ProcessingWorker(self.testing, folder, device_id, csv_path, slopes, room_temp_f)
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

    def _on_analysis_result(self, payload: dict) -> None:
        # Update cache if needed
        self._last_analysis_payload = payload
        if payload and payload.get("baseline"):
            worker = self.sender()
            if isinstance(worker, TemperatureAnalysisWorker) and worker.baseline_csv:
                 if self._cached_baseline_path != worker.baseline_csv:
                     self._cached_baseline_path = worker.baseline_csv
                     self._cached_baseline_result = payload.get("baseline")

        self.analysis_status.emit({"status": "completed", "message": "Analysis ready"})
        self.analysis_ready.emit(payload)

    def _on_analysis_error(self, message: str) -> None:
        self.analysis_status.emit({"status": "error", "message": message})
        self.processing_status.emit({"status": "error", "message": message})

    def configure_correction(self, payload: dict):
        """
        Configure backend temperature correction.
        payload: {
            'slopes': dict,
            'use_temperature_correction': bool,
            'room_temperature_f': float
        }
        """
        self.hardware.configure_temperature_correction(
            payload.get("slopes", {}),
            payload.get("use_temperature_correction", False),
            payload.get("room_temperature_f", 72.0)
        )

    def prepare_grid_display(self, payload: dict, stage_key: str) -> None:
        """
        Prepare grid cell display data from analysis payload and emit grid_display_ready.
        
        Args:
            payload: Analysis result from analyze_temperature_processed_runs
            stage_key: 'All', 'db', or 'bw'
        """
        if not payload:
            return

        grid_info = payload.get("grid", {})
        meta = payload.get("meta", {})
        body_weight_n = float(meta.get("body_weight_n") or 0.0)
        device_type = str(grid_info.get("device_type", "06"))

        baseline = payload.get("baseline", {})
        selected = payload.get("selected", {})

        display_data = {
            "grid_info": grid_info,
            "device_id": meta.get("device_id"),
            "baseline_cells": self._compute_cell_display(baseline, stage_key, device_type, body_weight_n),
            "selected_cells": self._compute_cell_display(selected, stage_key, device_type, body_weight_n),
        }
        
        self.grid_display_ready.emit(display_data)

    def _compute_cell_display(
        self, 
        data: dict, 
        stage_key: str, 
        device_type: str, 
        body_weight_n: float
    ) -> List[Dict]:
        """
        Compute display data for cells from analysis data.
        
        Returns list of dicts with: row, col, color_bin, text
        """
        stages = data.get("stages", {})
        cell_data: Dict[Tuple[int, int], Dict] = {}
        
        stage_keys = list(stages.keys()) if stage_key == "All" else [stage_key]
        
        for sk in stage_keys:
            stage_info = stages.get(sk, {})
            if not stage_info:
                continue
                
            target_n = float(stage_info.get("target_n", 0.0))
            threshold_n = config.get_passing_threshold(sk, device_type, body_weight_n)
            
            for cell in stage_info.get("cells", []):
                r = int(cell.get("row", 0))
                c = int(cell.get("col", 0))
                signed_pct = float(cell.get("signed_pct", 0.0))
                mean_n = float(cell.get("mean_n", 0.0))
                
                # Compute error ratio
                error_n = abs(mean_n - target_n)
                error_ratio = error_n / threshold_n if threshold_n > 0 else 0.0
                
                key = (r, c)
                if key not in cell_data:
                    cell_data[key] = {"signed_pcts": [], "error_ratios": []}
                cell_data[key]["signed_pcts"].append(signed_pct)
                cell_data[key]["error_ratios"].append(error_ratio)
        
        # Build result list
        result: List[Dict] = []
        for (r, c), info in cell_data.items():
            avg_pct = sum(info["signed_pcts"]) / len(info["signed_pcts"])
            avg_ratio = sum(info["error_ratios"]) / len(info["error_ratios"])
            
            result.append({
                "row": r,
                "col": c,
                "color_bin": config.get_color_bin(avg_ratio),
                "text": f"{avg_pct:+.1f}%",
            })
        
        return result

    def plot_stage_detection(self) -> None:
        """
        Emit signal to launch matplotlib visualization showing stage detection windows
        for both baseline and selected processed runs.
        """
        if not self._current_meta:
            self.processing_status.emit({"status": "error", "message": "No test loaded"})
            return
        
        baseline_path = self._current_baseline_path or ""
        selected_path = self._current_selected_path or ""
        
        # Fallback: find paths from processed runs if not set
        if not baseline_path:
            for run in self._current_processed_runs:
                if run.get("is_baseline"):
                    baseline_path = str(run.get("path") or "").strip()
                    break
        
        if not selected_path:
            for run in reversed(self._current_processed_runs):
                if not run.get("is_baseline"):
                    selected_path = str(run.get("path") or "").strip()
                    break
        
        if not baseline_path:
            self.processing_status.emit({"status": "error", "message": "No baseline CSV found"})
            return
        
        if not selected_path:
            selected_path = baseline_path  # Fall back to just showing baseline
        
        body_weight_n = float(self._current_meta.get("body_weight_n") or 800.0)
        
        # Retrieve cached window/segment info from last analysis if available
        baseline_windows = {}
        baseline_segments = []
        selected_windows = {}
        selected_segments = []
        
        if self._last_analysis_payload:
            base_data = self._last_analysis_payload.get("baseline") or {}
            sel_data = self._last_analysis_payload.get("selected") or {}
            baseline_windows = base_data.get("_windows") or {}
            baseline_segments = base_data.get("_segments") or []
            selected_windows = sel_data.get("_windows") or {}
            selected_segments = sel_data.get("_segments") or []
        
        # Emit signal to run plot on main thread
        self.plot_ready.emit({
            "baseline_path": baseline_path,
            "selected_path": selected_path,
            "body_weight_n": body_weight_n,
            "baseline_windows": baseline_windows,
            "baseline_segments": baseline_segments,
            "selected_windows": selected_windows,
            "selected_segments": selected_segments,
        })
