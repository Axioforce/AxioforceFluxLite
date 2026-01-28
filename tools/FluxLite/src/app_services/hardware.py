from __future__ import annotations
import threading
import time
from typing import Callable, Optional, Dict, Any, List
from PySide6 import QtCore

from .. import config
from ..io_client import IoClient
from ..domain.models import DeviceState, Device, LAUNCH_NAME, LANDING_NAME
import requests
from ..infra.backend_address import BackendAddress, backend_address_from_config

class HardwareService(QtCore.QObject):
    """
    Manages communication with the hardware backend via IoClient.
    Emits signals for data updates and connection status.
    """
    # Signals
    connection_status_changed = QtCore.Signal(str)  # "Connected", "Disconnected", "Connecting..."
    data_received = QtCore.Signal(dict)  # Raw JSON payload
    device_list_updated = QtCore.Signal(list)  # List of available devices
    active_devices_updated = QtCore.Signal(set)  # Set of device IDs actively streaming data
    config_status_received = QtCore.Signal(dict) # Dynamo config status

    # Model signals
    model_metadata_received = QtCore.Signal(object)
    model_package_status_received = QtCore.Signal(object)
    model_activation_status_received = QtCore.Signal(object)
    model_load_status_received = QtCore.Signal(object)

    # Error signals
    socket_error_received = QtCore.Signal(str)  # For socket.io errors

    def __init__(self):
        super().__init__()
        self.client: Optional[IoClient] = None
        self._http_host: Optional[str] = None
        self._http_port: Optional[int] = None
        self._socket_port: Optional[int] = None
        self._stop_flag = threading.Event()
        self._groups: List[dict] = []
        self._active_devices: set = set()
        self._connected_devices: set = set()

    def backend_http_address(self) -> BackendAddress:
        """
        Authoritative backend HTTP address for the current session.

        If we have a discovered/connected host+port, use it; otherwise fall back to config/env.
        """
        try:
            host = str(self._http_host or "").strip()
            port = int(self._http_port) if self._http_port else None
            if host and port:
                return BackendAddress(host=host, port=int(port))
        except Exception:
            pass
        return backend_address_from_config()
        
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
            # Realtime device connect/disconnect updates
            self.client.on("connectionStatusUpdate", self._on_connection_status_update)
            
            # Config & Model listeners
            self.client.on("getDynamoConfigStatus", lambda d: self.config_status_received.emit(d))
            self.client.on("modelMetadata", lambda d: self.model_metadata_received.emit(d))
            self.client.on("modelPackageStatus", lambda d: self.model_package_status_received.emit(d))
            self.client.on("modelActivationStatus", lambda d: self.model_activation_status_received.emit(d))
            self.client.on("modelLoadStatus", lambda d: self.model_load_status_received.emit(d))

            # Error event listener (socket.io standard error event)
            self.client.on("error", lambda d: self.socket_error_received.emit(str(d)))

            self.client.start()
            self.connection_status_changed.emit(f"Connecting to {host}:{port}...")

    def disconnect(self) -> None:
        if self.client:
            self.client.stop()
            self.client = None
        # Clear connection-derived state so UI can revert to empty state.
        try:
            self._connected_devices = set()
            self._active_devices = set()
            self.active_devices_updated.emit(set())
            self.device_list_updated.emit([])
        except Exception:
            pass
        self.connection_status_changed.emit("Disconnected")

    def _on_connect(self) -> None:
        # Force status update in case IoClient handler didn't run yet or failed
        if self.client:
            self.client.status.connected = True
            
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
        # Socket disconnected => no streaming.
        try:
            self._connected_devices = set()
            self._active_devices = set()
            self.active_devices_updated.emit(set())
            self.device_list_updated.emit([])
        except Exception:
            pass
        # If we disconnected unexpectedly, try to auto-connect again after a delay
        # But only if we aren't already trying.
        if not self.client:
             # If client is None, we manually disconnected. Don't auto-connect.
             return
             
        # If client exists but disconnected, it might be a blip, or backend restart.
        # IoClient will try to reconnect to SAME port.
        # But if backend changed ports, IoClient will fail forever.
        # So we should probably restart auto-connect logic after some time if it doesn't recover.
        pass

    def _on_json(self, data: dict) -> None:
        self.data_received.emit(data)
        
        # Track active devices from streaming data
        try:
            # We assume the payload might be a list of device frames or a dict with device data
            # Typically structure is { deviceId: {...}, ... } or [ { deviceId: ... }, ... ]
            # But let's look at how data comes in. Usually it's key-value or list.
            
            current_ids = set()
            
            if isinstance(data, list):
                for item in data:
                    did = item.get("deviceId") or item.get("id")
                    if did:
                        current_ids.add(str(did))
            elif isinstance(data, dict):
                # Check for direct keys or nested structures
                if "deviceId" in data:
                     current_ids.add(str(data["deviceId"]))
                else:
                    # Maybe keys are device IDs?
                    # Or maybe it's "devices": [...]
                    devs = data.get("devices")
                    if isinstance(devs, list):
                        for d in devs:
                             did = d.get("deviceId") or d.get("id")
                             if did:
                                 current_ids.add(str(did))
                    elif isinstance(devs, dict):
                        for k in devs.keys():
                            current_ids.add(str(k))
                            
            if current_ids:
                # If we found IDs, update our set. 
                # Note: This is a simplistic "live" view. 
                # Realistically we might want to decay "active" status if no data for X seconds.
                # But for now, we just emit what we see in this frame.
                # Or better: accumulate and emit periodically? 
                # User asked to "look at json.data to see which plates we are getting data from".
                # So let's just emit the set of IDs seen in this payload.
                # If the UI accumulates them or fades them, that's up to UI.
                # But typically active_devices_ready expects the CURRENTLY active set.
                
                # Let's accumulate into self._active_devices and clear via timer?
                # Or just emit what we see now?
                # If we just emit what we see now, the UI might flicker if data is interleaved.
                # Let's emit the union for now?
                pass
                
                # Actually, the user's request implies we should "mark them as live".
                # I'll emit the set of IDs found in this packet. The UI can handle persistence/fading if needed.
                self._active_devices = set(current_ids)
                self.active_devices_updated.emit(set(current_ids))

        except Exception:
            pass

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

    def _infer_device_type(self, axf_id: str, payload_hint: object | None = None) -> str:
        """
        Infer canonical device type ("06","07","08","11") from an axfId.
        Backends vary on whether deviceTypeId is present; this keeps UI stable.
        """
        try:
            s = str(axf_id or "").strip()
            if not s:
                return ""
            # Common formats: "07.00000051", "07-....", "07..."
            prefix = s[:2]
            if prefix in ("06", "07", "08", "11"):
                return prefix
        except Exception:
            pass
        # Fallback: attempt numeric deviceTypeId mapping if your backend uses it.
        try:
            dt = str(payload_hint or "").strip()
            # If backend already sent canonical type, keep it.
            if dt in ("06", "07", "08", "11"):
                return dt
        except Exception:
            pass
        return ""

    def _on_connection_status_update(self, payload: dict) -> None:
        """
        Server -> client realtime updates:
        { "<groupAxfId>": { "isConnected": true, "devices": { "<deviceAxfId>": true/false, ... } } }
        We use this to refresh the connected device list without polling.
        """
        try:
            connected: set[str] = set()
            if isinstance(payload, dict):
                for _gid, g in payload.items():
                    grp = g or {}
                    devs = grp.get("devices") or {}
                    if isinstance(devs, dict):
                        for dev_id, is_on in devs.items():
                            if bool(is_on):
                                connected.add(str(dev_id))
            self._connected_devices = connected
        except Exception:
            pass
        # If nothing is connected anymore, clear "active" immediately so UI can revert.
        if not self._connected_devices:
            try:
                self._active_devices = set()
                self.active_devices_updated.emit(set())
                self.device_list_updated.emit([])
            except Exception:
                pass
        # Pull an authoritative list (names/types) when connection state changes.
        try:
            if self.client:
                self.client.emit("getConnectedDevices")
        except Exception:
            pass

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

    def load_model(self, model_dir: str) -> None:
        """Load a model package file into both Firebase and local database."""
        if self.client:
            self.client.emit("loadModel", {"modelDir": str(model_dir)})

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
        # Parse payload to extract (name, axf_id, device_type) tuples.
        #
        # Supports both:
        # - legacy: { devices: [device,...] } or [device,...]
        # - current: [ { axfId, name, ..., devices: [device,...] }, ... ]
        devices: list[tuple[str, str, str]] = []
        try:
            raw_list: list = []
            if isinstance(payload, list):
                raw_list = payload
            elif isinstance(payload, dict):
                raw_list = payload.get("devices", []) or payload.get("groups", []) or []

            # If this looks like group objects, flatten their devices.
            flattened: list[dict] = []
            if raw_list and isinstance(raw_list[0], dict) and "devices" in raw_list[0] and ("isDeviceGroup" in raw_list[0] or "groupConfiguration" in raw_list[0]):
                for g in raw_list:
                    grp = g or {}
                    for d in (grp.get("devices") or []):
                        if isinstance(d, dict):
                            flattened.append(d)
            else:
                for d in raw_list:
                    if isinstance(d, dict):
                        flattened.append(d)

            for item in flattened:
                try:
                    axf_id = str(item.get("axfId") or item.get("deviceAxfId") or item.get("id") or item.get("deviceId") or "").strip()
                    if not axf_id:
                        continue
                    name = str(item.get("name") or item.get("deviceName") or "Unknown")
                    dt_hint = item.get("deviceTypeId") or item.get("deviceType") or item.get("type")
                    dt = self._infer_device_type(axf_id, dt_hint)
                    devices.append((name, axf_id, dt))
                except Exception:
                    continue
        except Exception:
            pass
        
        self.device_list_updated.emit(devices)

    def auto_connect(self, host: str = config.SOCKET_HOST, http_port: int = config.HTTP_PORT) -> None:
        """
        Attempt to automatically connect to the backend.
        Runs in a background thread.
        Stops once connected.
        """
        def _run():
            # Fallback ports to try if discovery fails
            fallback_ports = [3000]
            
            while not self._stop_flag.is_set():
                # If already connected, we are done.
                if self.client and self.client.status.connected:
                    self.connection_status_changed.emit("Connected")
                    return

                self.connection_status_changed.emit("Auto-connecting...")
                
                # 1. Try discovery
                port = self._discover_socket_port(host, http_port)
                if port:
                    self.connection_status_changed.emit(f"Found port {port}, connecting...")
                    self.connect(host, port)
                    # Wait for connection
                    for _ in range(25): # 5s
                        if self.client and self.client.status.connected:
                            return # Success! Exit thread.
                        time.sleep(0.2)
                    
                    # If we found a port via discovery but failed to connect, 
                    # we should probably NOT try fallbacks immediately, or maybe we should?
                    # Let's assume discovery is authoritative.
                
                # 2. Try fallback ports ONLY if not connected
                if not (self.client and self.client.status.connected):
                    for p in fallback_ports:
                        # Check again before trying next port
                        if self.client and self.client.status.connected:
                            return # Success!
                        
                        self.connection_status_changed.emit(f"Trying port {p}...")
                        try:
                            self.connect(host, p)
                            # Wait for connection
                            for _ in range(25): # 5s
                                if self.client and self.client.status.connected:
                                    return # Success!
                                time.sleep(0.2)
                        except Exception:
                            pass
                
                if self.client and self.client.status.connected:
                    return
                    
                self.connection_status_changed.emit("Retrying in 5s...")
                
                # Disconnect to clean up before next attempt (stops the previous IoClient thread)
                self.disconnect()
                time.sleep(5)

        threading.Thread(target=_run, daemon=True).start()

    def _discover_socket_port(self, host: str, http_port: int, timeout_s: float = 0.7) -> Optional[int]:
        """Attempt to discover the socket.io port by querying the backend HTTP config."""
        try:
            base = host.strip()
            if not base.startswith("http://") and not base.startswith("https://"):
                base = f"http://{base}"
            if base.endswith('/'):
                base = base[:-1]

            candidates = [
                "config",
                "dynamo/config",
                "api/config",
                "flux/config",
                "v1/config",
                "backend/config",
            ]

            def _find_socket_port(obj: Any) -> Optional[int]:
                try:
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            key = str(k).lower()
                            if "socketport" in key or ("socket" in key and "port" in key):
                                try:
                                    port_val = int(v)
                                    if 1000 <= port_val <= 65535:
                                        return port_val
                                except Exception:
                                    pass
                            found = _find_socket_port(v)
                            if found is not None:
                                return found
                    elif isinstance(obj, list):
                        for item in obj:
                            found = _find_socket_port(item)
                            if found is not None:
                                return found
                except Exception:
                    pass
                return None

            headers = {"Accept": "application/json"}
            for path in candidates:
                try:
                    url = f"{base}:{http_port}/{path}"
                    resp = requests.get(url, headers=headers, timeout=timeout_s)
                    if resp.status_code != 200:
                        continue
                    data = None
                    try:
                        data = resp.json()
                    except Exception:
                        continue
                    port = _find_socket_port(data)
                    if port is not None:
                        return port
                except Exception:
                    continue
        except Exception:
            return None
        return None

