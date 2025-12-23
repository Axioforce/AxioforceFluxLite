from __future__ import annotations
from PySide6 import QtCore
from typing import Optional, List, Dict

from .hardware import HardwareService

class ModelService(QtCore.QObject):
    """
    Service for managing Machine Learning models on the backend.
    Handles packaging, activation, deactivation, and metadata retrieval.
    """
    # Signals
    metadata_received = QtCore.Signal(list) # List of model metadata dicts
    package_status_received = QtCore.Signal(dict)
    load_status_received = QtCore.Signal(dict)
    activation_status_received = QtCore.Signal(dict)

    def __init__(self, hardware_service: HardwareService):
        super().__init__()
        self._hardware = hardware_service
        
        # Connect to hardware signals
        self._hardware.model_metadata_received.connect(self._on_metadata)
        self._hardware.model_package_status_received.connect(self.package_status_received.emit)
        self._hardware.model_activation_status_received.connect(self.activation_status_received.emit)
        # Note: HardwareService might need to expose load_status if it exists in backend events

    def request_metadata(self, device_id: str) -> None:
        """Request metadata for models associated with a device."""
        self._hardware.request_model_metadata(device_id)

    def package_model(self, force_dir: str, moments_dir: str, output_dir: str) -> None:
        """Request backend to package a model."""
        payload = {
            "forceModelDir": force_dir,
            "momentsModelDir": moments_dir,
            "outputDir": output_dir
        }
        self._hardware.package_model(payload)

    def activate_model(self, device_id: str, model_id: str) -> None:
        """Activate a specific model on a device."""
        self._hardware.activate_model(device_id, model_id)

    def deactivate_model(self, device_id: str, model_id: str) -> None:
        """Deactivate a specific model on a device."""
        self._hardware.deactivate_model(device_id, model_id)

    def set_bypass(self, enabled: bool) -> None:
        """Enable or disable global model bypass."""
        self._hardware.set_model_bypass(enabled)

    def _on_metadata(self, data: dict | list) -> None:
        """Process and emit model metadata."""
        entries = list(data or []) if isinstance(data, list) else [data]
        # logic for logging or processing could go here
        self.metadata_received.emit(entries)
