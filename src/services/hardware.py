from __future__ import annotations
import threading
import time
from typing import Callable, Optional, Dict, Any, List
from PySide6 import QtCore

from .. import config
from ..io_client import IoClient
from ..domain.models import DeviceState, Device, LAUNCH_NAME, LANDING_NAME

class HardwareService(QtCore.QObject):
    """
    Manages communication with the hardware backend via IoClient.
    Emits signals for data updates and connection status.
    """
    # Signals
    connection_status_changed = QtCore.Signal(str)  # "Connected", "Disconnected", "Connecting..."
    data_received = QtCore.Signal(dict)  # Raw JSON payload
    device_list_updated = QtCore.Signal(list)  # List of available devices
    config_status_received = QtCore.Signal(dict) # Dynamo config status
    
    # Model signals
    model_metadata_received = QtCore.Signal(object)
    model_package_status_received = QtCore.Signal(object)
    model_activation_status_received = QtCore.Signal(object)

    def __init__(self):
        super().__init__()
        self.client: Optional[IoClient] = None
        self._http_host: Optional[str] = None
        self._http_port: Optional[int] = None
        self._socket_port: Optional[int] = None
        self._stop_flag = threading.Event()
        self._groups: List[dict] = []
        
    def connect(self, host: str, port: int) -> None:
        self.disconnect()
        self.client = IoClient(host, port)
        self.client.set_json_callback(self._on_json)
        
        self._http_host = host
        try:
            self._socket_port = int(port)
        except Exception:
            self._socket_port = None
            
        # Infer HTTP port
        try:
            import os
            self._http_port = int(os.environ.get("HTTP_PORT", str(config.HTTP_PORT)))
        except Exception:
            try:
                self._http_port = int(getattr(config, "HTTP_PORT", 5000))
            except Exception:
                self._http_port = 5000
        
        # Fallback HTTP port
        if not self._http_port and self._socket_port:
            self._http_port = int(self._socket_port) + 1

        # Register listeners
        if self.client:
            self.client.on("connect", self._on_connect)
            self.client.on("disconnect", self._on_disconnect)
            self.client.on("getDeviceSettingsStatus", self._on_device_settings)
            self.client.on("getDeviceTypesStatus", self._on_device_types)
            self.client.on("getGroupDefinitionsStatus", self._on_group_definitions)
            self.client.on("groupDefinitions", self._on_group_definitions)
            self.client.on("connectedDeviceList", self._on_connected_device_list)
            
            # Config & Model listeners
            self.client.on("getDynamoConfigStatus", lambda d: self.config_status_received.emit(d))
            self.client.on("modelMetadata", lambda d: self.model_metadata_received.emit(d))
            self.client.on("modelPackageStatus", lambda d: self.model_package_status_received.emit(d))
            self.client.on("modelActivationStatus", lambda d: self.model_activation_status_received.emit(d))

            self.client.start()
            self.connection_status_changed.emit(f"Connecting to {host}:{port}...")

    def disconnect(self) -> None:
        if self.client:
            self.client.stop()
            self.client = None
        self.connection_status_changed.emit("Disconnected")

    def _on_connect(self) -> None:
        self.connection_status_changed.emit("Connected")
        if self.client:
            try:
                self.client.emit("getDynamoConfig")
                # One-time wakeup
                self._wakeup_backend()
                self.fetch_discovery()
            except Exception:
                pass

    def _on_disconnect(self, *args) -> None:
        self.connection_status_changed.emit("Disconnected")

    def _on_json(self, data: dict) -> None:
        self.data_received.emit(data)

    def _wakeup_backend(self) -> None:
        # Implementation of _wakeup_backend logic from original controller
        # This might need to be adapted if it depends on specific internal state
        pass

    def fetch_discovery(self) -> None:
        if self.client:
            self.client.emit("getDeviceSettings", {})
            self.client.emit("getDeviceTypes", {})
            self.client.emit("getGroupDefinitions", {})
            self.client.emit("getConnectedDevices")

    # --- Command Methods ---

    def start_capture(self, payload: dict) -> None:
        if self.client:
            capture_config = payload.get("capture_configuration", "simple")
            p = {
                "captureConfiguration": capture_config,
                "captureType": capture_config,
                "groupId": payload.get("group_id", ""),
                "athleteId": payload.get("athlete_id", ""),
            }
            if payload.get("capture_name"):
                p["captureName"] = payload["capture_name"]
            if payload.get("tags"):
                p["tags"] = payload["tags"]
            self.client.emit("startCapture", p)

    def stop_capture(self, payload: dict) -> None:
        if self.client:
            p = {"groupId": payload.get("group_id", "")}
            self.client.emit("stopCapture", p)

    def tare(self, group_id: str | None = None) -> None:
        if self.client:
            try:
                self.client.emit("setReferenceTime", -1)
            except Exception:
                pass
            self.client.emit("tareAll")

    def update_dynamo_config(self, key: str, value: object) -> None:
        if self.client:
            self.client.emit("updateDynamoConfig", {"key": str(key), "value": value})

    def set_model_bypass(self, enabled: bool) -> None:
        if self.client:
            self.client.emit("setModelBypass", bool(enabled))

    def request_model_metadata(self, device_id: str) -> None:
        if self.client:
            self.client.emit("getModelMetadata", {"deviceId": str(device_id)})

    def package_model(self, payload: dict) -> None:
        if self.client:
             self.client.emit("packageModel", {
                "forceModelDir": payload.get("forceModelDir", ""),
                "momentsModelDir": payload.get("momentsModelDir", ""),
                "outputDir": payload.get("outputDir", ""),
            })

    def activate_model(self, device_id: str, model_id: str) -> None:
        if self.client:
            self.client.emit("activateModel", {"deviceId": str(device_id), "modelId": str(model_id)})

    def deactivate_model(self, device_id: str, model_id: str) -> None:
        if self.client:
            self.client.emit("deactivateModel", {"deviceId": str(device_id), "modelId": str(model_id)})

    # --- Device & Group Logic ---

    def _normalize_device_id(self, s: str | None) -> str:
        if not s:
            return ""
        return str(s).strip().lower().replace("-", "")

    def resolve_group_id_for_device(self, device_id: str) -> Optional[str]:
        """Return group axfId that includes the provided device id, if available."""
        did_norm = self._normalize_device_id(device_id)
        if not did_norm or not self._groups:
            return None
        
        for g in self._groups:
            try:
                grp = g or {}
                gid = str(grp.get("axfId") or grp.get("axf_id") or grp.get("id") or "").strip()
                if not gid:
                    continue
                
                # Check devices
                devices = grp.get("devices") or []
                for d in (devices if isinstance(devices, list) else []):
                    cand = str(d.get("axfId") or d.get("id") or d.get("deviceId") or d.get("device_id") or "").strip()
                    if cand and self._normalize_device_id(cand) == did_norm:
                        return gid
                
                # Check mappings
                mappings = grp.get("mappings") or []
                for m in (mappings if isinstance(mappings, list) else []):
                    cand = str(m.get("deviceId") or m.get("device_id") or "").strip()
                    if cand and self._normalize_device_id(cand) == did_norm:
                        return gid
                
                # Check members
                members = grp.get("members") or []
                for m in (members if isinstance(members, list) else []):
                    cand = str(m.get("deviceId") or m.get("device_id") or m.get("axfId") or m.get("id") or "").strip()
                    if cand and self._normalize_device_id(cand) == did_norm:
                        return gid
            except Exception:
                continue
        return None

    def configure_temperature_correction(self, slopes: dict, enabled: bool, room_temp_f: float) -> None:
        """
        Configure backend temperature correction settings.
        """
        if not self.client:
            return
            
        # Update slopes
        # slopes dict expected: { 'x': float, 'y': float, 'z': float }
        self.update_dynamo_config("temperatureCorrectionSlopes", slopes)
        
        # Update room temp
        self.client.emit("setDeviceConfig", {"roomTemperatureF": float(room_temp_f)})
        
        # Update enabled state
        self.update_dynamo_config("applyTemperatureCorrection", bool(enabled))

    # --- Discovery Handlers ---
    
    def _on_device_settings(self, payload: dict) -> None:
        # Process device settings and emit update
        pass

    def _on_device_types(self, payload: dict) -> None:
        pass

    def _on_group_definitions(self, payload: dict) -> None:
        try:
            if isinstance(payload, list):
                self._groups = payload
            elif isinstance(payload, dict) and "groups" in payload:
                self._groups = payload["groups"]
            else:
                self._groups = []
        except Exception:
            self._groups = []

    def _on_connected_device_list(self, payload: dict | list) -> None:
        # Parse payload to extract (name, axf_id, device_type) tuples
        devices = []
        try:
            raw_list = payload if isinstance(payload, list) else payload.get("devices", [])
            for item in raw_list:
                try:
                    name = str(item.get("name") or "Unknown")
                    axf_id = str(item.get("axfId") or item.get("id") or "")
                    # Extract device type from deviceTypeId or similar
                    dt = str(item.get("deviceTypeId") or "")
                    # Fallback logic for type if needed (e.g. from name or ID)
                    if not dt and "-" in axf_id:
                        # heuristic
                        pass
                    
                    if axf_id:
                        devices.append((name, axf_id, dt))
                except Exception:
                    continue
        except Exception:
            pass
        
        self.device_list_updated.emit(devices)

