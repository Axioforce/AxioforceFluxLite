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

    # Mound group signals
    mound_group_created = QtCore.Signal(object)  # DeviceGroup dict with axfId
    mound_group_found = QtCore.Signal(object)    # Existing DeviceGroup dict
    mound_group_error = QtCore.Signal(str)       # Error message

    def __init__(self):
        super().__init__()
        self.client: Optional[IoClient] = None
        self._http_host: Optional[str] = None
        self._http_port: Optional[int] = None
        self._socket_port: Optional[int] = None
        self._stop_flag = threading.Event()
        # Connected groups (device groups created/saved in the backend)
        self._groups: List[dict] = []
        # Group definitions (schemas like "PitchingMound", "DualLaunchPad", etc.)
        self._group_definitions: List[dict] = []
        self._active_devices: set = set()
        self._connected_devices: set = set()
        # Track last-seen timestamps for each device to implement decay
        self._device_last_seen: Dict[str, float] = {}
        self._active_device_decay_s: float = 1.0  # Device considered inactive after 1 second of no data
        # Cross-thread Qt scheduling:
        # Socket callbacks may run on non-Qt threads; never start Qt timers from there.
        self._qt_call_lock = threading.Lock()
        self._qt_call_queue: list[Callable[[], None]] = []

    @QtCore.Slot()
    def _drain_qt_call_queue(self) -> None:
        """
        Run queued callables on the Qt (GUI) thread.

        Socket.io callbacks can run on background threads. Anything that starts a Qt timer
        (e.g. QTimer.singleShot) must be initiated from a thread with a running Qt event loop.
        """
        while True:
            fn: Optional[Callable[[], None]] = None
            with self._qt_call_lock:
                if self._qt_call_queue:
                    fn = self._qt_call_queue.pop(0)
            if fn is None:
                return
            try:
                fn()
            except Exception:
                pass

    def _post_to_qt(self, fn: Callable[[], None]) -> None:
        """Schedule `fn` to run on this QObject's Qt thread."""
        try:
            with self._qt_call_lock:
                self._qt_call_queue.append(fn)
            QtCore.QMetaObject.invokeMethod(self, "_drain_qt_call_queue", QtCore.Qt.QueuedConnection)
        except Exception:
            try:
                fn()
            except Exception:
                pass

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
        try:
            print(f"[hardware] connect: host={host} port={port}")
        except Exception:
            pass
        
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
            # Live stream control (DynamoDeluxe-style backends require this)
            self.client.on("startDataReceptionStatus", lambda d: print(f"[hardware] startDataReceptionStatus: {d}"))
            self.client.on("getDeviceSettingsStatus", self._on_device_settings)
            self.client.on("getDeviceTypesStatus", self._on_device_types)
            self.client.on("getGroupDefinitionsStatus", self._on_group_definitions)
            self.client.on("groupDefinitions", self._on_group_definitions)
            # Connected groups (actual group instances)
            self.client.on("getConnectedGroupsStatus", self._on_connected_groups)
            # Legacy backends use getGroups/getGroupsStatus for connected group list.
            self.client.on("getGroupsStatus", self._on_connected_groups)
            self.client.on("connectedGroupList", self._on_connected_groups)  # some backends use a push-style event
            self.client.on("connectedDeviceList", self._on_connected_device_list)
            # Realtime device connect/disconnect updates
            self.client.on("connectionStatusUpdate", self._on_connection_status_update)

            # Optional maintenance events (debug visibility)
            self.client.on("reinitializeDeviceGroupsStatus", lambda d: print(f"[hardware] reinitializeDeviceGroupsStatus: {d}"))
            self.client.on("reinitializeConnectedDevicesStatus", lambda d: print(f"[hardware] reinitializeConnectedDevicesStatus: {d}"))
            
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
            try:
                self.client.status.last_connect_time = time.time()
            except Exception:
                pass
            try:
                url = getattr(self.client, "_url", None) or f"{self._http_host or ''}:{self._socket_port or ''}"
                print(f"[hardware] socket connected: {url}")
            except Exception:
                pass
            
        self.connection_status_changed.emit("Connected")
        if self.client:
            try:
                # IMPORTANT: Many DynamoDeluxe-style backends do not emit `jsonData`
                # until a client explicitly starts data reception.
                try:
                    self.client.emit("startDataReception", {})
                except Exception:
                    pass
                self.client.emit("getDynamoConfig")
                # One-time wakeup
                self._wakeup_backend()
                self.fetch_discovery()
            except Exception:
                pass

    def _on_disconnect(self, *args) -> None:
        self.connection_status_changed.emit("Disconnected")
        if self.client:
            try:
                self.client.status.connected = False
                self.client.status.last_disconnect_time = time.time()
                url = getattr(self.client, "_url", None) or f"{self._http_host or ''}:{self._socket_port or ''}"
                print(f"[hardware] socket disconnected: {url}")
            except Exception:
                pass
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

        # Track active devices from streaming data with decay-based accumulation
        try:
            now = time.time()
            current_ids: set[str] = set()

            # Extract device IDs from various payload formats
            if isinstance(data, list):
                for item in data:
                    did = item.get("deviceId") or item.get("device_id") or item.get("id")
                    if did:
                        current_ids.add(str(did))
            elif isinstance(data, dict):
                # Single-frame payload (common): jsonData emits { deviceId: "...", ... }
                if "deviceId" in data or "device_id" in data or "id" in data:
                    did = data.get("deviceId") or data.get("device_id") or data.get("id")
                    if did:
                        current_ids.add(str(did))
                else:
                    devs = data.get("devices")
                    if isinstance(devs, list):
                        for d in devs:
                            did = d.get("deviceId") or d.get("device_id") or d.get("id")
                            if did:
                                current_ids.add(str(did))
                    elif isinstance(devs, dict):
                        for k in devs.keys():
                            current_ids.add(str(k))

            # Update last-seen timestamps for devices in this packet
            for did in current_ids:
                self._device_last_seen[did] = now

            # Build active set: all devices seen within decay window
            active_ids: set[str] = set()
            stale_ids: list[str] = []
            for did, last_seen in self._device_last_seen.items():
                if (now - last_seen) <= self._active_device_decay_s:
                    active_ids.add(did)
                else:
                    stale_ids.append(did)

            # Clean up stale entries
            for did in stale_ids:
                del self._device_last_seen[did]

            # Only emit if the active set changed
            if active_ids != self._active_devices:
                self._active_devices = set(active_ids)
                self.active_devices_updated.emit(set(active_ids))
                try:
                    print(f"[hardware] active_devices_updated: {len(active_ids)} -> {sorted(active_ids)}")
                except Exception:
                    pass

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
            # Connected groups (optional, but required to detect existing mound configs by mappings)
            try:
                self.client.emit("getConnectedGroups")
                # Legacy backends
                self.client.emit("getGroups")
            except Exception:
                pass

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

    # --- Mound Group Management ---

    def find_or_create_mound_group(
        self,
        launch_device_id: str,
        upper_landing_device_id: str,
        lower_landing_device_id: str,
        group_name: str = "Pitching Mound",
        *,
        create_if_missing: bool = True,
    ) -> None:
        """
        Check if a pitching mound group already exists with these devices.
        If found, emit mound_group_found. If not, create one and emit mound_group_created.
        """
        if not self.client:
            self.mound_group_error.emit("Not connected to backend")
            return

        # Normalize device IDs for comparison
        launch_norm = self._normalize_device_id(launch_device_id)
        upper_norm = self._normalize_device_id(upper_landing_device_id)
        lower_norm = self._normalize_device_id(lower_landing_device_id)

        # Check existing connected groups for a matching pitching mound
        for g in (self._groups or []):
            try:
                cfg = str(
                    g.get("groupConfiguration")
                    or g.get("group_configuration")
                    or g.get("configuration")
                    or g.get("configurationId")
                    or g.get("configuration_id")
                    or ""
                ).lower()
                # Accept either human configuration strings or the canonical definition ID.
                if ("pitching" not in cfg and "mound" not in cfg and "pitchingmound" not in cfg):
                    # Some backends nest the group definition on the object
                    def_id = str(g.get("groupDefinitionId") or g.get("group_definition_id") or "").lower()
                    if "pitchingmound" not in def_id:
                        continue

                # Extract device mappings from this group
                mappings = g.get("mappings") or []
                group_devices: Dict[str, str] = {}  # position -> normalized device_id
                for m in mappings:
                    pos = str(m.get("position") or m.get("positionId") or m.get("position_id") or "").strip()
                    did = str(m.get("deviceId") or m.get("device_id") or "").strip()
                    if pos and did:
                        group_devices[pos] = self._normalize_device_id(did)

                # Check if all three positions match
                if (
                    group_devices.get("Launch Zone") == launch_norm
                    and group_devices.get("Upper Landing Zone") == upper_norm
                    and group_devices.get("Lower Landing Zone") == lower_norm
                ):
                    print(f"[hardware] Found existing mound group: {g.get('axfId') or g.get('groupId')}")
                    self.mound_group_found.emit(g)
                    return
            except Exception:
                continue

        # No existing group found
        if not create_if_missing:
            return

        print(
            f"[hardware] Creating new pitching mound group with devices: "
            f"{launch_device_id}, {upper_landing_device_id}, {lower_landing_device_id}"
        )
        self._create_mound_group(
            launch_device_id,
            upper_landing_device_id,
            lower_landing_device_id,
            group_name,
        )

    def _create_mound_group(
        self,
        launch_device_id: str,
        upper_landing_device_id: str,
        lower_landing_device_id: str,
        group_name: str,
    ) -> None:
        """Create a new pitching mound device group via socket."""
        if not self.client:
            return
        if not bool(getattr(self.client.status, "connected", False)):
            self.mound_group_error.emit("Not connected (socket.io) - cannot create mound group")
            return

        # Register handler for the response (and allow a retry if no response arrives)
        done = {"v": False}

        def _request_reinitialize_device_groups() -> None:
            """
            Legacy backends persist the group but won't "activate" it (build runtime multi-device groups / virtual devices)
            until devices are regrouped. DynamoDeluxe exposes `reinitializeDeviceGroups`; some older backends expose
            `reinitializeConnectedDevices`.
            """
            try:
                if not self.client or not bool(getattr(self.client.status, "connected", False)):
                    return
                try:
                    print("[hardware] emit: reinitializeDeviceGroups")
                    self.client.emit("reinitializeDeviceGroups")
                    return
                except Exception:
                    pass
                try:
                    print("[hardware] emit: reinitializeConnectedDevices")
                    self.client.emit("reinitializeConnectedDevices")
                except Exception:
                    pass
            except Exception:
                pass

        def _recheck_after_reinit() -> None:
            # Pull fresh groups, then try to resolve without re-creating.
            try:
                self.fetch_discovery()
            except Exception:
                pass
            try:
                self.find_or_create_mound_group(
                    launch_device_id=launch_device_id,
                    upper_landing_device_id=upper_landing_device_id,
                    lower_landing_device_id=lower_landing_device_id,
                    group_name=group_name,
                    create_if_missing=False,
                )
            except Exception:
                pass

        def on_create_status(payload: dict) -> None:
            try:
                done["v"] = True
                if not isinstance(payload, dict):
                    self.mound_group_created.emit(payload)
                    return

                # Many backends wrap responses as {status, message, response, ...}
                inner = payload.get("response") if isinstance(payload.get("response"), (dict, list)) else None
                group_obj = inner if isinstance(inner, dict) else payload

                axf_id = group_obj.get("axfId") or group_obj.get("axf_id") or group_obj.get("groupId") or group_obj.get("id")
                if axf_id:
                    print(f"[hardware] Mound group created: {axf_id}")
                    self.mound_group_created.emit(group_obj)
                    # Refresh groups list
                    self.fetch_discovery()
                    return

                # Legacy saveGroup responses often look like:
                # { status: "success", message: "Group saved successfully. Please restart..." }
                status = str(payload.get("status") or "").strip().lower()
                msg = payload.get("message")
                err = payload.get("error") or (group_obj.get("error") if isinstance(group_obj, dict) else None)

                if status == "success" and not err:
                    # createTemporaryGroup returns a success status with { data: { group_id, ... } }.
                    data_obj = payload.get("data") if isinstance(payload.get("data"), dict) else None
                    tmp_group_id = None
                    if data_obj:
                        tmp_group_id = data_obj.get("group_id") or data_obj.get("groupId") or data_obj.get("axfId")
                    if tmp_group_id:
                        # Construct a minimal group object so the UI can store mound_group_id immediately.
                        created = {
                            "axfId": str(tmp_group_id),
                            "name": str(group_name),
                            "groupDefinitionId": str(def_id),
                            "mappings": [
                                {
                                    "positionId": m.get("position_id"),
                                    "mappingIndex": m.get("mapping_index"),
                                    "deviceId": m.get("device_id"),
                                    "rotation": m.get("rotation", 0),
                                }
                                for m in (mappings or [])
                            ],
                        }
                        print(f"[hardware] Temporary mound group created: {tmp_group_id}")
                        self.mound_group_created.emit(created)
                        # Refresh groups list so the runtime group appears in connected groups.
                        try:
                            self._post_to_qt(lambda: QtCore.QTimer.singleShot(500, self.fetch_discovery))
                        except Exception:
                            pass
                        return

                    # If this was a legacy saveGroup response, activate by reinitializing device groups.
                    try:
                        if isinstance(msg, str) and ("restart" in msg.lower() or "saved successfully" in msg.lower()):
                            print("[hardware] saveGroup success received; reinitializing device groups to activate mappings")
                            _request_reinitialize_device_groups()
                            # Give the backend time to rebuild groups, then refresh + resolve.
                            self._post_to_qt(lambda: QtCore.QTimer.singleShot(1500, _recheck_after_reinit))
                            self._post_to_qt(lambda: QtCore.QTimer.singleShot(3000, _recheck_after_reinit))
                    except Exception:
                        pass
                    return

                if err:
                    self.mound_group_error.emit(str(err))
                    return
                if isinstance(msg, str) and msg:
                    self.mound_group_error.emit(msg)
                    return
                self.mound_group_created.emit(group_obj)
            except Exception as e:
                self.mound_group_error.emit(str(e))

        # Different backend generations use different event names.
        # - Newer: createDeviceGroup/createDeviceGroupStatus (returns group object)
        # - Legacy: saveGroup/groupUpdateStatus (often returns just a status message)
        self.client.on("createDeviceGroupStatus", on_create_status)
        self.client.on("groupUpdateStatus", on_create_status)

        # Build the creation payload to match DynamoDeluxe schema:
        # DeviceGroupCreationData + DeviceGroupMapping (snake_case keys).
        #
        # IMPORTANT: mapping_index values must match the group definition.
        # For PitchingMound, DynamoDeluxe reports:
        # - Launch Zone: mapping_index 0, rotation -90
        # - Upper Landing Zone: mapping_index 1, rotation 0
        # - Lower Landing Zone: mapping_index 2, rotation 0
        #
        # We derive these from group definitions if available; otherwise fall back to the known defaults.
        def_id = "PitchingMound"
        req: list[dict] = []
        try:
            for gd in (self._group_definitions or []):
                try:
                    axf_id = str(gd.get("axf_id") or gd.get("axfId") or "").strip()
                    name = str(gd.get("name") or gd.get("group_definition_name") or gd.get("groupDefinitionName") or "").strip().lower()
                except Exception:
                    continue
                if axf_id == "PitchingMound" or ("pitching" in name and "mound" in name):
                    def_id = axf_id or "PitchingMound"
                    req = list(gd.get("required_group_positions") or gd.get("requiredGroupPositions") or gd.get("required_devices") or gd.get("requiredDevices") or [])
                    break
        except Exception:
            req = []

        # Default required positions if backend didn't send definitions in the expected shape.
        if not req:
            req = [
                {"position_id": "Launch Zone", "mapping_index": 0, "rotation": -90},
                {"position_id": "Upper Landing Zone", "mapping_index": 1, "rotation": 0},
                {"position_id": "Lower Landing Zone", "mapping_index": 2, "rotation": 0},
            ]

        # Map selected devices to positions.
        by_pos = {
            "Launch Zone": str(launch_device_id),
            "Upper Landing Zone": str(upper_landing_device_id),
            "Lower Landing Zone": str(lower_landing_device_id),
        }

        mappings: list[dict] = []
        for r in req:
            try:
                pos = str(r.get("position_id") or r.get("positionId") or "").strip()
                midx = int(r.get("mapping_index") or r.get("mappingIndex") or 0)
                rot = int(r.get("rotation") or 0)
            except Exception:
                continue
            did = str(by_pos.get(pos) or "").strip()
            if not pos or not did:
                continue
            mappings.append(
                {
                    "position_id": pos,
                    "mapping_index": midx,
                    "device_id": did,
                    "rotation": rot,
                }
            )

        # Build a camelCase payload matching the known-working frontend:
        # { groupDefinitionId, name, disableVirtualDevices, mappings:[{positionId,mappingIndex,deviceId,(rotation?)}] }
        payload_camel = {
            "groupDefinitionId": def_id,
            "name": str(group_name),
            "disableVirtualDevices": False,
            "mappings": [
                {
                    "positionId": m.get("position_id"),
                    "mappingIndex": m.get("mapping_index"),
                    "deviceId": m.get("device_id"),
                    # Keep rotation explicit; backend will default to 0 if omitted.
                    "rotation": m.get("rotation", 0),
                }
                for m in mappings
            ],
        }

        # Also keep a snake_case variant for backends that validate that shape directly.
        payload_snake = {
            "group_definition_id": def_id,
            "name": str(group_name),
            "disable_virtual_devices": False,
            "mappings": mappings,
        }

        # Prefer runtime-only group creation when available (no Firebase/DB persistence).
        payload_temp = {
            "group_definition_id": def_id,
            "name": str(group_name),
            "disable_virtual_devices": False,
            "mappings": mappings,
        }

        print(f"[hardware] Emitting createTemporaryGroup: {payload_temp}")
        self.client.emit("createTemporaryGroup", payload_temp)

        # If the backend never responds, retry once (common when the socket drops mid-action).
        def _retry_once() -> None:
            try:
                if done["v"]:
                    return
                if not self.client or not bool(getattr(self.client.status, "connected", False)):
                    return
                # Fallback retry order:
                # - createDeviceGroup (newer DynamoDeluxe)
                # - saveGroup (older DynamoPy)
                print("[hardware] createTemporaryGroup: no status received; retrying once via createDeviceGroup")
                try:
                    self.client.emit("createDeviceGroup", payload_camel)
                    return
                except Exception:
                    pass
                print("[hardware] createDeviceGroup: retrying once via saveGroup (snake_case payload)")
                self.client.emit("saveGroup", payload_snake)
            except Exception:
                pass

        try:
            QtCore.QTimer.singleShot(1200, _retry_once)
        except Exception:
            pass

    # --- Discovery Handlers ---
    
    def _on_device_settings(self, payload: dict) -> None:
        # Process device settings and emit update
        pass

    def _on_device_types(self, payload: dict) -> None:
        pass

    def _on_group_definitions(self, payload: dict) -> None:
        try:
            data = payload
            # unwrap common status message wrapper
            if isinstance(payload, dict) and ("response" in payload or "data" in payload):
                data = payload.get("response") or payload.get("data") or []
            if isinstance(data, list):
                self._group_definitions = data
            elif isinstance(data, dict) and "groups" in data:
                self._group_definitions = list(data.get("groups") or [])
            else:
                self._group_definitions = []
        except Exception:
            self._group_definitions = []

    def _on_connected_groups(self, payload: dict | list) -> None:
        """
        Store connected group instances (not definitions).

        Backends often wrap these in a status envelope: {status, message, response: [...]}
        """
        try:
            data = payload
            if isinstance(payload, dict) and ("response" in payload or "data" in payload):
                data = payload.get("response") or payload.get("data") or []
            if isinstance(data, list):
                self._groups = data
            elif isinstance(data, dict) and "groups" in data:
                self._groups = list(data.get("groups") or [])
            else:
                self._groups = []
        except Exception:
            self._groups = []

        # Also emit a flattened "available devices" list for UI pickers.
        # DynamoDeluxe sends connected devices as groups via getConnectedGroupsStatus (no connectedDeviceList).
        try:
            self._on_connected_device_list(self._groups)
        except Exception:
            pass

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
                # Unwrap common status message wrapper and alternate keys.
                inner = payload.get("response") or payload.get("data") or payload
                if isinstance(inner, dict):
                    raw_list = inner.get("devices", []) or inner.get("groups", []) or []
                elif isinstance(inner, list):
                    raw_list = inner
                else:
                    raw_list = payload.get("devices", []) or payload.get("groups", []) or []

            # If this looks like group objects, flatten their devices.
            flattened: list[dict] = []
            if (
                raw_list
                and isinstance(raw_list[0], dict)
                and "devices" in raw_list[0]
                and (
                    "isDeviceGroup" in raw_list[0]
                    or "groupConfiguration" in raw_list[0]
                    or "is_device_group" in raw_list[0]
                    or "group_configuration" in raw_list[0]
                )
            ):
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
                    axf_id = str(
                        item.get("axfId")
                        or item.get("axf_id")
                        or item.get("deviceAxfId")
                        or item.get("device_axf_id")
                        or item.get("id")
                        or item.get("deviceId")
                        or item.get("device_id")
                        or ""
                    ).strip()
                    if not axf_id:
                        continue
                    name = str(item.get("name") or item.get("deviceName") or "Unknown")
                    dt_hint = (
                        item.get("deviceTypeId")
                        or item.get("device_type_id")
                        or item.get("deviceType")
                        or item.get("device_type")
                        or item.get("type")
                    )
                    dt = self._infer_device_type(axf_id, dt_hint)
                    devices.append((name, axf_id, dt))
                except Exception:
                    continue
        except Exception:
            pass

        print(f"[hardware] device_list_updated: {len(devices)} devices -> {devices}")
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

