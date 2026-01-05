from __future__ import annotations
from PySide6 import QtCore
import os
import threading
from typing import Optional, Dict, List

# Use relative imports assuming this file is in src/ui/controllers/
from ...calibration.processor import process_45v, process_ols, process_tls

class CalibrationWorker(QtCore.QThread):
    """Background worker for heatmap generation."""
    heatmap_ready = QtCore.Signal(str, dict) # tag, result
    finished_all = QtCore.Signal()

    def __init__(self, paths: Dict[str, str], model_id: str, plate_type: str, device_id: str):
        super().__init__()
        self.paths = paths
        self.model_id = model_id
        self.plate_type = plate_type
        self.device_id = device_id

    def run(self):
        # 45V
        if self.paths.get("45V"):
            try:
                res = process_45v(self.paths["45V"], self.model_id, self.plate_type, self.device_id)
                self.heatmap_ready.emit("45V", res)
            except Exception as e:
                self.heatmap_ready.emit("45V", {"error": str(e)})
        
        # OLS
        if self.paths.get("OLS"):
            try:
                res = process_ols(self.paths["OLS"], self.model_id, self.plate_type, self.device_id)
                self.heatmap_ready.emit("OLS", res)
            except Exception as e:
                self.heatmap_ready.emit("OLS", {"error": str(e)})

        # TLS
        if self.paths.get("TLS"):
            try:
                res = process_tls(self.paths["TLS"], self.model_id, self.plate_type, self.device_id)
                self.heatmap_ready.emit("TLS", res)
            except Exception as e:
                self.heatmap_ready.emit("TLS", {"error": str(e)})
        
        self.finished_all.emit()

class CalibrationController(QtCore.QObject):
    """
    Controller for Calibration Heatmap generation.
    """
    # Signals
    status_updated = QtCore.Signal(str) # Status message
    heatmap_ready = QtCore.Signal(str, dict) # tag, data
    files_loaded = QtCore.Signal(bool) # whether valid files were found

    def __init__(self):
        super().__init__()
        self._paths: Dict[str, str] = {}
        self._worker: Optional[CalibrationWorker] = None

    def load_folder(self, folder_path: str) -> None:
        """Scan folder for calibration CSVs."""
        if not folder_path or not os.path.isdir(folder_path):
            self.status_updated.emit("Invalid folder")
            self.files_loaded.emit(False)
            return

        self._paths = {}
        # Simple heuristic matching
        for f in os.listdir(folder_path):
            if not f.lower().endswith(".csv"):
                continue
            full = os.path.join(folder_path, f)
            lower = f.lower()
            if "45v" in lower:
                self._paths["45V"] = full
            elif "ols" in lower:
                self._paths["OLS"] = full
            elif "tls" in lower:
                self._paths["TLS"] = full
        
        found = list(self._paths.keys())
        if found:
            self.status_updated.emit(f"Loaded: {', '.join(found)}")
            self.files_loaded.emit(True)
        else:
            self.status_updated.emit("No calibration files found (looking for 45V/OLS/TLS)")
            self.files_loaded.emit(False)

    def generate_heatmaps(self, model_id: str, plate_type: str, device_id: str) -> None:
        """Run generation in background."""
        if not self._paths:
            self.status_updated.emit("No files loaded")
            return

        if self._worker and self._worker.isRunning():
            self.status_updated.emit("Busy...")
            return

        self.status_updated.emit("Generating...")
        self._worker = CalibrationWorker(self._paths, model_id, plate_type, device_id)
        self._worker.heatmap_ready.connect(self.heatmap_ready.emit)
        self._worker.finished_all.connect(self._on_finished)
        self._worker.start()

    def _on_finished(self):
        self.status_updated.emit("Generation Complete")
        self._worker = None


