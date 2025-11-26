from __future__ import annotations
from PySide6 import QtCore
from typing import Optional, List

from ...services.testing import TestingService
from ...services.hardware import HardwareService

class ProcessingWorker(QtCore.QThread):
    """Worker thread for running temperature processing in the background."""
    def __init__(self, service: TestingService, folder: str, device_id: str, csv_path: str, slopes: dict):
        super().__init__()
        self.service = service
        self.folder = folder
        self.device_id = device_id
        self.csv_path = csv_path
        self.slopes = slopes

    def run(self):
        self.service.run_temperature_processing(self.folder, self.device_id, self.csv_path, self.slopes)

class TempTestController(QtCore.QObject):
    """
    Controller for the Temperature Testing UI.
    Manages test file listing, processing, and configuration.
    """
    # Signals for View
    tests_listed = QtCore.Signal(list) # list of file paths
    devices_listed = QtCore.Signal(list) # list of device IDs
    processing_status = QtCore.Signal(dict) # forwarded from service
    processing_status = QtCore.Signal(dict) # forwarded from service

    def __init__(self, testing_service: TestingService, hardware_service: HardwareService):
        super().__init__()
        self.testing = testing_service
        self.hardware = hardware_service
        
        # Forward service signals
        self.testing.processing_status.connect(self.processing_status.emit)
        
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
            'folder': str (optional, default to parent of csv_path)
        }
        """
        device_id = payload.get("device_id")
        csv_path = payload.get("csv_path")
        slopes = payload.get("slopes", {})
        
        if not device_id or not csv_path:
            return
            
        import os
        folder = payload.get("folder") or os.path.dirname(csv_path)
        
        # Run in background
        if self._worker and self._worker.isRunning():
            self.processing_status.emit({"status": "error", "message": "Processing already in progress"})
            return

        self._worker = ProcessingWorker(self.testing, folder, device_id, csv_path, slopes)
        # Clean up worker reference when done
        self._worker.finished.connect(lambda: setattr(self, '_worker', None))
        self._worker.start()

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
