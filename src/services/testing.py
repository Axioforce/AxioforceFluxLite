from __future__ import annotations
import os
import logging
import requests
import json
from typing import Optional, List, Dict, Tuple, Any

from PySide6 import QtCore

from .. import config
from .hardware import HardwareService
from .session_manager import SessionManager
from .device_geometry_service import DeviceGeometryService
from .repositories.test_file_repository import TestFileRepository
from .analysis.temperature_analyzer import TemperatureAnalyzer
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
        self.geometry = DeviceGeometryService()
        self.repo = TestFileRepository()
        self.analyzer = TemperatureAnalyzer(self.geometry)
        self.session_manager = SessionManager(self.geometry)

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

    def start_session(self, tester_name: str, device_id: str, model_id: str, body_weight_n: float, thresholds: TestThresholds, is_temp_test: bool = False, is_discrete_temp: bool = False) -> TestSession:
        return self.session_manager.start_session(
            tester_name, device_id, model_id, body_weight_n, thresholds, is_temp_test, is_discrete_temp
        )

    def end_session(self) -> None:
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

    # --- Orchestration ---

    def run_temperature_processing(self, folder: str, device_id: str, csv_path: str, slopes: dict, room_temp_f: float = 72.0, mode: str = "legacy") -> None:
        """
        Orchestrates temperature processing by using Repository methods.
        """
        if not os.path.isfile(csv_path):
            self.processing_status.emit({"status": "error", "message": f"File not found: {csv_path}"})
            return

        if not self._hardware:
            self.processing_status.emit({"status": "error", "message": "Hardware service unavailable for temperature correction"})
            return

        try:
            paths = self.repo.derive_temperature_paths(csv_path, device_id, mode)

            slopes_name = self.repo.formatted_slope_name(slopes)
            processed_on_name = paths["processed_on_template"].format(slopes=slopes_name)

            if os.path.isfile(paths["trimmed"]):
                trimmed_path = paths["trimmed"]
                self.processing_status.emit({"status": "running", "message": "Using existing 50Hz CSV...", "progress": 5})
            else:
                self.processing_status.emit({"status": "running", "message": "Slimming CSV to 50Hz...", "progress": 5})
                trimmed_path = self.repo.downsample_csv_to_50hz(csv_path, paths["trimmed"])

            self.processing_status.emit({"status": "running", "message": "Checking baseline...", "progress": 25})
            
            processed_off_path = os.path.join(folder, paths["processed_off_name"])
            if os.path.isfile(processed_off_path):
                self.processing_status.emit({"status": "running", "message": "Using existing baseline...", "progress": 25})
            else:
                self.processing_status.emit({"status": "running", "message": "Processing (temp correction off)...", "progress": 25})
                # Using internal method
                self._call_backend_process_csv(
                    trimmed_path,
                    device_id,
                    folder,
                    paths["processed_off_name"],
                    use_temperature_correction=False,
                    room_temp_f=room_temp_f,
                    slopes=None, 
                )

            self.processing_status.emit({"status": "running", "message": "Processing (temp correction on)...", "progress": 65})
            
            self._call_backend_process_csv(
                trimmed_path,
                device_id,
                folder,
                processed_on_name,
                use_temperature_correction=True,
                room_temp_f=room_temp_f,
                slopes=slopes,
                mode=mode,
            )

            self.repo.update_meta_with_processed(
                paths["meta"],
                trimmed_path,
                os.path.join(folder, paths["processed_off_name"]),
                os.path.join(folder, processed_on_name),
                slopes,
                mode,
            )

            self.processing_status.emit({"status": "completed", "message": "Temperature processing complete", "progress": 100})
        except Exception as e:
            self.processing_status.emit({"status": "error", "message": str(e)})

    def _call_backend_process_csv(
        self,
        input_csv_path: str,
        device_id: str,
        output_folder: str,
        output_filename: str,
        use_temperature_correction: bool,
        room_temp_f: float,
        slopes: Optional[dict] = None,
        mode: str = "legacy",
    ) -> None:
        if not os.path.isfile(input_csv_path):
            raise FileNotFoundError(f"Input CSV not found: {input_csv_path}")

        host = config.SOCKET_HOST
        port = config.HTTP_PORT
        # If hardware service discovered a port, use it
        if self._hardware and self._hardware._http_port:
             port = self._hardware._http_port
             if self._hardware._http_host:
                 host = self._hardware._http_host

        # Ensure scheme
        if not host.startswith("http"):
            host = f"http://{host}"
        
        # Clean host of existing port and trailing slash
        host = host.rstrip("/")
        try:
            # simple split to remove port if present
            # e.g. http://localhost:3000 -> http://localhost
            head, tail = host.split("://", 1)
            if ":" in tail:
                host = f"{head}://{tail.split(':')[0]}"
        except Exception:
            pass

        # Using /api/device/process-csv as observed in offline_runner.py
        url = f"{host}:{port}/api/device/process-csv"
        
        # Prepare payload for backend (JSON body with paths)
        # Backend expected to be on localhost/same filesystem
        body = {
            'csvPath': os.path.abspath(input_csv_path),
            'deviceId': device_id,
            'outputDir': os.path.abspath(output_folder),
            'use_temperature_correction': bool(use_temperature_correction),
            'room_temperature_f': float(room_temp_f),
            'mode': mode
        }
        
        if slopes:
            vals = {
                'x': float(slopes.get('x', 0)),
                'y': float(slopes.get('y', 0)),
                'z': float(slopes.get('z', 0))
            }
            if mode == "scalar":
                body['temperature_correction_coefficients'] = vals
            else:
                body['temperature_correction_slopes'] = vals

        try:
            logger.info(f"POST {url} with body keys: {list(body.keys())}")
            # Match offline_runner.py behavior (json.dumps + specific headers) just in case
            headers = {"Content-Type": "application/json"}
            response = requests.post(url, data=json.dumps(body), headers=headers, timeout=300) 
            response.raise_for_status()
            
            # The backend writes the file to disk and returns the path
            data = response.json() or {}
            out_csv_path = data.get("outputPath") or data.get("path") or data.get("processed_csv")
            
            if not out_csv_path or not os.path.isfile(out_csv_path):
                # Fallback: check if the backend wrote to the expected location? 
                # Or maybe it failed silently to write but returned success?
                # We can't rely on response content since this endpoint returns JSON info.
                logger.warning(f"Backend returned output path: {out_csv_path}, but file not found?")
            else:
                # Rename/Move to expected output_filename if different
                expected_path = os.path.join(output_folder, output_filename)

                if os.path.abspath(out_csv_path) != os.path.abspath(expected_path):
                    try:
                        if os.path.exists(expected_path):
                            os.remove(expected_path)
                        os.rename(out_csv_path, expected_path)
                    except Exception as move_err:
                        logger.error(f"Failed to move processed file to expected name: {move_err}")

        except Exception as e:
            logger.error(f"Backend processing failed: {e}")
            raise e

