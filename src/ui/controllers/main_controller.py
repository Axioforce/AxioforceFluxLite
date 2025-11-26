from __future__ import annotations
from PySide6 import QtCore

from ...services.hardware import HardwareService
from ...services.testing import TestingService
from ...services.data_sync import DataSyncService
from ...services.model_service import ModelService
from .live_test_controller import LiveTestController
from .temp_test_controller import TempTestController

class MainController(QtCore.QObject):
    """
    Main controller for the application.
    Coordinates services and provides a central access point for application logic.
    """
    def __init__(self):
        super().__init__()
        self.hardware = HardwareService()
        self.testing = TestingService()
        self.data_sync = DataSyncService()
        self.models = ModelService(self.hardware)
        
        self.live_test = LiveTestController(self.testing)
        self.temp_test = TempTestController(self.testing, self.hardware)

    def start(self):
        """Initialize services and start background tasks."""
        # Connect hardware signals to any global handlers if needed
        self.hardware.auto_connect()

    def shutdown(self):
        """Cleanup and shutdown services."""
        self.hardware.disconnect()
