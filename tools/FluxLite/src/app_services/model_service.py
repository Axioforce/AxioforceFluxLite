from __future__ import annotations
import os
import glob
from pathlib import Path
from PySide6 import QtCore
from typing import Optional, List, Dict

from .hardware import HardwareService


# Timeout in milliseconds for metadata requests
METADATA_REQUEST_TIMEOUT_MS = 10000  # 10 seconds


class ModelService(QtCore.QObject):
    """
    Service for managing Machine Learning models on the backend.
    Handles packaging, activation, deactivation, and metadata retrieval.
    """
    # Signals
    metadata_received = QtCore.Signal(list)  # List of model metadata dicts
    metadata_error = QtCore.Signal(str)  # Error message when metadata request fails
    package_status_received = QtCore.Signal(dict)
    load_status_received = QtCore.Signal(dict)
    activation_status_received = QtCore.Signal(dict)

    def __init__(self, hardware_service: HardwareService):
        super().__init__()
        self._hardware = hardware_service
        self._pending_package_output_dir: Optional[str] = None
        self._pending_package_force_dir: Optional[str] = None
        self._metadata_request_pending: bool = False
        self._metadata_timeout_timer: Optional[QtCore.QTimer] = None

        # Connect to hardware signals
        self._hardware.model_metadata_received.connect(self._on_metadata)
        self._hardware.model_package_status_received.connect(self._on_package_status)
        self._hardware.model_activation_status_received.connect(self.activation_status_received.emit)
        self._hardware.model_load_status_received.connect(self._on_load_status)

        # Connect to socket error signal for better error handling
        self._hardware.socket_error_received.connect(self._on_socket_error)

    def request_metadata(self, device_id: str) -> None:
        """Request metadata for models associated with a device."""
        # Start timeout timer
        self._metadata_request_pending = True
        self._start_metadata_timeout()
        self._hardware.request_model_metadata(device_id)

    def package_model(self, force_dir: str, moments_dir: str, output_dir: str) -> None:
        """Request backend to package a model."""
        # Store for auto-load after successful package
        self._pending_package_output_dir = output_dir
        self._pending_package_force_dir = force_dir
        payload = {
            "forceModelDir": force_dir,
            "momentsModelDir": moments_dir,
            "outputDir": output_dir
        }
        self._hardware.package_model(payload)

    def load_model(self, model_dir: str) -> None:
        """Load a model package file into both Firebase and local database."""
        self._hardware.load_model(model_dir)

    def activate_model(self, device_id: str, model_id: str) -> None:
        """Activate a specific model on a device."""
        self._hardware.activate_model(device_id, model_id)

    def deactivate_model(self, device_id: str, model_id: str) -> None:
        """Deactivate a specific model on a device."""
        self._hardware.deactivate_model(device_id, model_id)

    def set_bypass(self, enabled: bool) -> None:
        """Enable or disable global model bypass."""
        self._hardware.set_model_bypass(enabled)

    def _start_metadata_timeout(self) -> None:
        """Start a timeout timer for metadata requests."""
        self._cancel_metadata_timeout()
        self._metadata_timeout_timer = QtCore.QTimer(self)
        self._metadata_timeout_timer.setSingleShot(True)
        self._metadata_timeout_timer.timeout.connect(self._on_metadata_timeout)
        self._metadata_timeout_timer.start(METADATA_REQUEST_TIMEOUT_MS)

    def _cancel_metadata_timeout(self) -> None:
        """Cancel any pending metadata timeout."""
        if self._metadata_timeout_timer is not None:
            try:
                self._metadata_timeout_timer.stop()
                self._metadata_timeout_timer.deleteLater()
            except Exception:
                pass
            self._metadata_timeout_timer = None

    def _on_metadata_timeout(self) -> None:
        """Handle metadata request timeout - backend may have crashed."""
        if self._metadata_request_pending:
            self._metadata_request_pending = False
            error_msg = (
                "Model metadata request timed out. "
                "The backend may have encountered an error. "
                "Try repackaging or reloading the model."
            )
            print(f"[ModelService] {error_msg}")
            self.metadata_error.emit(error_msg)

    def _on_metadata(self, data: dict | list) -> None:
        """Process and emit model metadata."""
        # Cancel timeout - we got a response
        self._metadata_request_pending = False
        self._cancel_metadata_timeout()

        entries = list(data or []) if isinstance(data, list) else [data]
        self.metadata_received.emit(entries)

    def _on_package_status(self, data: dict) -> None:
        """Handle package status and auto-load on success to sync local DB."""
        status = data.get("status", "") if isinstance(data, dict) else ""

        if status == "success" and self._pending_package_output_dir and self._pending_package_force_dir:
            # Try to find and load the packaged model to sync local database
            model_path = self._find_packaged_model()
            if model_path:
                print(f"[ModelService] Auto-loading packaged model: {model_path}")
                self.load_model(model_path)

        # Clear pending state
        self._pending_package_output_dir = None
        self._pending_package_force_dir = None

        # Emit to listeners
        self.package_status_received.emit(data)

    def _on_load_status(self, data: dict) -> None:
        """Handle load status response."""
        self.load_status_received.emit(data)

    def _on_socket_error(self, error: str) -> None:
        """Handle socket errors from the hardware service."""
        print(f"[ModelService] Socket error received: {error}")
        # If we have a pending metadata request, treat this as a failure
        if self._metadata_request_pending:
            self._metadata_request_pending = False
            self._cancel_metadata_timeout()
            self.metadata_error.emit(f"Socket error: {error}")

    def _find_packaged_model(self) -> Optional[str]:
        """
        Find the most recently created .axf-tfpkg file in the output directory.

        Dynamo saves packaged models to: {output_dir}/combined_model/*.axf-tfpkg
        """
        if not self._pending_package_output_dir:
            return None

        combined_model_dir = os.path.join(self._pending_package_output_dir, "combined_model")
        if not os.path.isdir(combined_model_dir):
            return None

        # Find all .axf-tfpkg files and get the most recent one
        pattern = os.path.join(combined_model_dir, "*.axf-tfpkg")
        pkg_files = glob.glob(pattern)

        if not pkg_files:
            return None

        # Return the most recently modified file
        try:
            most_recent = max(pkg_files, key=os.path.getmtime)
            return most_recent
        except Exception:
            return None
