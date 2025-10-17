from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from . import config
from .io_client import IoClient
from .model import Model, DeviceState


class Controller:
    def __init__(self, view: object) -> None:
        self.view = view
        self.model = Model()
        self.client: Optional[IoClient] = None
        self._stop_flag = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._single_snapshot_latest: Optional[tuple] = None
        self._wakeup_sent: bool = False
        self._single_state: Optional[DeviceState] = None
        self._single_last_selected_id: str = ""
        self._active_devices: dict[str, float] = {}  # device_id -> last_seen_timestamp
        self._state_lock = threading.Lock()
        self._overrun_count = 0
        # Dynamic UI tick rate (Hz)
        try:
            self._ui_tick_hz: int = int(getattr(config, "UI_TICK_HZ", 60))
        except Exception:
            self._ui_tick_hz = 60
        self._pending_force_vector: Optional[tuple[str, int, float, float, float]] = None  # (device_id, t_ms, fx, fy, fz)
        self._pending_moment_vectors: dict[str, tuple[int, float, float, float]] = {}
        self._pending_mound_force_vectors: dict[str, tuple[int, float, float, float]] = {}
        
        # Pitching mound configuration
        self._mound_definition: Optional[dict] = None
        self._mound_group_id: Optional[str] = None
        self._mound_configuration_id: Optional[str] = None
        self._mound_selected_devices: dict[str, str] = {}  # position_id -> device_id

        # Wire view events
        if hasattr(self.view, "on_connect_clicked"):
            self.view.on_connect_clicked(self.connect)
        if hasattr(self.view, "on_disconnect_clicked"):
            self.view.on_disconnect_clicked(self.disconnect)
        if hasattr(self.view, "on_flags_changed"):
            self.view.on_flags_changed(lambda: None)
        # Mirror config changes to avoid reading UI from worker threads
        if hasattr(self.view, "on_config_changed"):
            self.view.on_config_changed(self._on_config_changed)
        if hasattr(self.view, "on_start_capture"):
            self.view.on_start_capture(self.start_capture)
        if hasattr(self.view, "on_stop_capture"):
            self.view.on_stop_capture(self.stop_capture)
        if hasattr(self.view, "on_tare"):
            self.view.on_tare(self.tare)
        # Model management hooks from view
        if hasattr(self.view, "on_request_model_metadata"):
            self.view.on_request_model_metadata(self.request_model_metadata)
        if hasattr(self.view, "on_package_model"):
            self.view.on_package_model(self.package_model)
        if hasattr(self.view, "on_activate_model"):
            self.view.on_activate_model(self.activate_model)
        if hasattr(self.view, "on_deactivate_model"):
            self.view.on_deactivate_model(self.deactivate_model)
        # Optional: discovery handlers wiring
        if hasattr(self.view, "on_request_discovery"):
            self.view.on_request_discovery(self.fetch_discovery)
        # Wire mound device selection
        if hasattr(self.view, "on_mound_device_selected"):
            self.view.on_mound_device_selected(self.set_mound_device)

    def _normalize_device_id(self, s: str | None) -> str:
        t = (s or "").strip()
        if not t:
            return ""
        # Remove non-alphanumeric to handle '-' vs '.' differences
        norm = "".join(ch for ch in t if ch.isalnum())
        return norm

    # Mirror of selected device id to avoid worker reading UI state
    def _on_config_changed(self) -> None:
        try:
            if hasattr(self.view, "state"):
                # Cache normalized selected id and reset single smoother if selection changed
                raw_id = getattr(self.view.state, "selected_device_id", "") or ""
                normalized = self._normalize_device_id(raw_id)
                if self._single_last_selected_id != normalized:
                    self._single_last_selected_id = normalized
                    self._single_state = None
                    with self._state_lock:
                        self._pending_force_vector = None
                        self._pending_mound_force_vectors.clear()
        except Exception:
            pass

    # Socket data callback
    def _on_json(self, data: dict) -> None:
        # Always update rate estimate from incoming payloads
        try:
            self.model.update_rate_from_payload(data)
        except Exception:
            pass

        # Track active devices
        dev_id = data.get("device_id") or data.get("deviceId")
        if dev_id:
            with self._state_lock:
                self._active_devices[str(dev_id)] = time.time()
        
        with self._state_lock:
            pos = self.model.update_from_payload(data, alpha=config.SMOOTH_ALPHA, fz_threshold=config.FZ_THRESHOLD_N)
        # Track last-seen device id per plate for labeling
        if pos is not None:
            if isinstance(dev_id, str) and hasattr(self.view, "bridge"):
                try:
                    self.view.bridge.plate_device_id_ready.emit(pos, str(dev_id))
                except Exception:
                    pass
        # Single-device rendering path (own smoother using same EWMA behavior)
        try:
            if hasattr(self.view, "state") and getattr(self.view.state, "display_mode", "") == "single":
                # Use mirrored normalized id captured on config change
                selected_id = self._single_last_selected_id
                incoming_id = self._normalize_device_id(str(data.get("deviceId") or data.get("device_id") or ""))
                if selected_id and incoming_id and incoming_id == selected_id:
                    # Reset smoother if selection changed
                    if self._single_last_selected_id != selected_id or self._single_state is None:
                        self._single_state = DeviceState()
                        self._single_last_selected_id = selected_id
                    # Compute Fz total (prefer Sum)
                    sensors = data.get("sensors") or []
                    fx_total = 0.0
                    fy_total = 0.0
                    fz_total = 0.0
                    sum_entry = None
                    for s in sensors:
                        try:
                            if str(s.get("name", "")).strip().lower() == "sum":
                                sum_entry = s
                                break
                        except Exception:
                            continue
                    if sum_entry is not None:
                        try:
                            fx_total = float(sum_entry.get("x", 0.0))
                            fy_total = float(sum_entry.get("y", 0.0))
                            fz_total = float(sum_entry.get("z", 0.0))
                        except Exception:
                            fx_total = 0.0
                            fy_total = 0.0
                            fz_total = 0.0
                    else:
                        for s in sensors:
                            try:
                                fx_total += float(s.get("x", 0.0))
                                fy_total += float(s.get("y", 0.0))
                                fz_total += float(s.get("z", 0.0))
                            except Exception:
                                continue
                    cop = data.get("cop") or {}
                    x_mm = float(cop.get("x", 0.0)) * 1000.0
                    y_mm = float(cop.get("y", 0.0)) * 1000.0
                    time_ms = int(data.get("time", 0))
                    # Update smoother and visibility using same config as mound path
                    if self._single_state is not None:
                        self._single_state.raw_cop_x_mm = x_mm
                        self._single_state.raw_cop_y_mm = y_mm
                        self._single_state.update(x_mm, y_mm, fz_total, time_ms, alpha=config.SMOOTH_ALPHA)
                        self._single_state.is_visible = abs(fz_total) >= config.FZ_THRESHOLD_N
                        snap = self._single_state.snapshot()
                        # Cache latest single snapshot for UI thread to pull at tick
                        self._single_snapshot_latest = snap
                        # Cache latest force vector; emit on tick
                        with self._state_lock:
                            self._pending_force_vector = (str(dev_id or ""), int(time_ms), float(fx_total), float(fy_total), float(fz_total))
                else:
                    # Ignore non-matching messages; keep last snapshot to avoid flicker
                    pass
        except Exception:
            # Non-fatal; keep streaming
            pass

        # Mound-mode per-zone force vector capture (Launch vs Landing)
        try:
            if hasattr(self.view, "state") and getattr(self.view.state, "display_mode", "") == "mound":
                # Compute force totals (prefer Sum entry)
                sensors = data.get("sensors") or []
                fx_total = 0.0
                fy_total = 0.0
                fz_total = 0.0
                sum_entry = None
                for s in sensors:
                    try:
                        if str(s.get("name", "")).strip().lower() == "sum":
                            sum_entry = s
                            break
                    except Exception:
                        continue
                if sum_entry is not None:
                    try:
                        fx_total = float(sum_entry.get("x", 0.0))
                        fy_total = float(sum_entry.get("y", 0.0))
                        fz_total = float(sum_entry.get("z", 0.0))
                    except Exception:
                        fx_total = 0.0
                        fy_total = 0.0
                        fz_total = 0.0
                else:
                    for s in sensors:
                        try:
                            fx_total += float(s.get("x", 0.0))
                            fy_total += float(s.get("y", 0.0))
                            fz_total += float(s.get("z", 0.0))
                        except Exception:
                            continue
                time_ms = int(data.get("time", 0))
                if pos is not None:
                    with self._state_lock:
                        self._pending_mound_force_vectors[pos] = (time_ms, fx_total, fy_total, fz_total)
                    # Defer per-zone forces to throttled tick loop
        except Exception:
            pass

        # Capture moments for all devices (individual and virtual) for Moments View
        try:
            m = data.get("moments") or {}
            mx = float(m.get("x", 0.0))
            my = float(m.get("y", 0.0))
            mz = float(m.get("z", 0.0))
            t_ms = int(data.get("time", 0))
            did = str(data.get("deviceId") or data.get("device_id") or "").strip()
            if did:
                with self._state_lock:
                    self._pending_moment_vectors[did] = (t_ms, mx, my, mz)
                # Defer moments to throttled tick loop
        except Exception:
            pass

        # Defer snapshots/active devices to throttled tick loop

    def connect(self, host: str, port: int) -> None:
        self.disconnect()
        self.client = IoClient(host, port)
        self.client.set_json_callback(self._on_json)
        
        # Attach discovery listeners BEFORE starting client
        if self.client is not None:
            try:
                self.client.on("getDeviceSettingsStatus", self._on_device_settings)
                self.client.on("getDeviceTypesStatus", self._on_device_types)
                # Group definitions (some servers emit 'groupDefinitions' without Status suffix)
                self.client.on("getGroupDefinitionsStatus", self._on_group_definitions)
                self.client.on("groupDefinitions", self._on_group_definitions)
                # Fallback discovery for older/newer backends
                self.client.on("connectedDeviceList", self._on_connected_device_list)
            except Exception:
                pass

        # Wakeup backend on first successful connect
        self._wakeup_sent = False
        if self.client is not None:
            def _on_first_connect() -> None:
                # On connect, fetch backend config (samplingRate/emissionRate)
                try:
                    self.client.emit("getDynamoConfig")
                except Exception:
                    pass
                # One-time backend wakeup
                if not self._wakeup_sent:
                    self._wakeup_sent = True
                    try:
                        self._wakeup_backend()
                    except Exception:
                        pass
                # Fetch group definitions for mound configuration
                try:
                    print("[ctrl] Fetching group definitions on connect...")
                    self.fetch_discovery()
                except Exception:
                    pass
                # Update connection status
                try:
                    if hasattr(self.view, "bridge"):
                        self.view.bridge.connection_text_ready.emit("Connected")
                except Exception:
                    pass
            try:
                self.client.on("connect", _on_first_connect)
                # Listen for config status to forward to UI
                self.client.on("getDynamoConfigStatus", self._on_dynamo_config_status)
                # Model management listeners
                self.client.on("modelMetadata", self._on_model_metadata)
                self.client.on("modelPackageStatus", self._on_model_package_status)
                self.client.on("modelLoadStatus", self._on_model_load_status)
                self.client.on("modelActivationStatus", self._on_model_activation_status)
                # Connection text on disconnect
                self.client.on("disconnect", lambda *_: getattr(self.view, "bridge", None) and self.view.bridge.connection_text_ready.emit("Disconnected"))
            except Exception:
                pass
        
        # NOW start the client after listeners are registered
        self.client.start()
        # Start throttled UI tick loop
        try:
            if self._thread is None or not self._thread.is_alive():
                self._stop_flag.clear()
                self._thread = threading.Thread(target=self._tick_loop, daemon=True)
                self._thread.start()
        except Exception:
            pass
        if hasattr(self.view, "bridge"):
            try:
                self.view.bridge.connection_text_ready.emit(f"Connecting to {host}:{port}...")
            except Exception:
                pass

        # No-op

    def disconnect(self) -> None:
        if self.client is not None:
            self.client.stop()
            self.client = None
        if hasattr(self.view, "bridge"):
            try:
                self.view.bridge.connection_text_ready.emit("Disconnected")
            except Exception:
                pass

    # Control emits
    def start_capture(self, payload: dict) -> None:
        if self.client is not None:
            p = {
                "captureConfiguration": payload.get("capture_configuration", "manual"),  # or "pitch"
                "groupId": payload.get("group_id", ""),
                "athleteId": payload.get("athlete_id", ""),
            }
            if payload.get("capture_name"):
                p["captureName"] = payload["capture_name"]
            if payload.get("tags"):
                p["tags"] = payload["tags"]
            self.client.emit("startCapture", p)

    def stop_capture(self, payload: dict) -> None:
        if self.client is not None:
            p = {"groupId": payload.get("group_id", "")}
            self.client.emit("stopCapture", p)

    def tare(self, group_id: str | None = None) -> None:
        if self.client is None:
            return
        # Reset reference time before taring
        try:
            self.client.emit("setReferenceTime", -1)
        except Exception:
            pass
        # Tare all devices/groups (backend handles per-group internally)
        try:
            self.client.emit("tareAll")
            try:
                print("[ctrl] TareAll emitted")
            except Exception:
                pass
        except Exception:
            pass

    # Discovery API
    def fetch_discovery(self) -> None:
        if self.client is None:
            return
        self.client.emit("getDeviceSettings", {})
        self.client.emit("getDeviceTypes", {})
        self.client.emit("getGroupDefinitions", {})
        # Fallback discovery event used by some backends
        self.client.emit("getConnectedDevices")

    # Config API
    def request_sampling_rate(self, rate_hz: int) -> None:
        if self.client is not None:
            try:
                self.client.emit("setSamplingRate", int(rate_hz))
            except Exception:
                pass

    def request_emission_rate(self, rate_hz: int) -> None:
        if self.client is not None:
            try:
                self.client.emit("setDataEmissionRate", int(rate_hz))
            except Exception:
                pass

    # --- Model management -------------------------------------------------------
    def request_model_metadata(self, device_id: str) -> None:
        if self.client is not None and (device_id or "").strip():
            try:
                self.client.emit("getModelMetadata", {"deviceId": str(device_id)})
            except Exception:
                pass

    def package_model(self, payload: dict) -> None:
        if self.client is not None and isinstance(payload, dict):
            try:
                self.client.emit("packageModel", {
                    "forceModelDir": payload.get("forceModelDir", ""),
                    "momentsModelDir": payload.get("momentsModelDir", ""),
                    "outputDir": payload.get("outputDir", ""),
                })
            except Exception:
                pass

    def activate_model(self, device_id: str, model_id: str) -> None:
        if self.client is not None and (device_id or "").strip() and (model_id or "").strip():
            try:
                self.client.emit("activateModel", {"deviceId": str(device_id), "modelId": str(model_id)})
            except Exception:
                pass

    def deactivate_model(self, device_id: str, model_id: str) -> None:
        if self.client is not None and (device_id or "").strip() and (model_id or "").strip():
            try:
                self.client.emit("deactivateModel", {"deviceId": str(device_id), "modelId": str(model_id)})
            except Exception:
                pass

    def _on_model_metadata(self, data: dict | list) -> None:
        try:
            # Log concise backend payload summary for debugging
            try:
                entries = list(data or []) if isinstance(data, list) else [data]
            except Exception:
                entries = []
            parts = []
            for m in entries:
                try:
                    mid = str((m or {}).get("modelId") or (m or {}).get("model_id") or "?")
                    dev = str((m or {}).get("deviceId") or (m or {}).get("device_id") or "?")
                    loc = str((m or {}).get("location") or "").strip() or "-"
                    act = bool((m or {}).get("active")) or bool((m or {}).get("model_active"))
                    parts.append(f"{dev}:{mid}({'on' if act else 'off'},{loc})")
                except Exception:
                    continue
            try:
                print(f"[ctrl] model_metadata received: {len(entries)} entries [{', '.join(parts)}]")
            except Exception:
                pass
            if hasattr(self.view, "bridge"):
                self.view.bridge.model_metadata_ready.emit(data)
        except Exception:
            pass

    def _on_model_package_status(self, status: dict) -> None:
        try:
            if hasattr(self.view, "bridge"):
                self.view.bridge.model_package_status_ready.emit(status)
        except Exception:
            pass

    def _on_model_load_status(self, status: dict) -> None:
        try:
            if hasattr(self.view, "bridge"):
                self.view.bridge.model_load_status_ready.emit(status)
        except Exception:
            pass

    def _on_model_activation_status(self, status: dict) -> None:
        try:
            if hasattr(self.view, "bridge"):
                self.view.bridge.model_activation_status_ready.emit(status)
        except Exception:
            pass

    def _on_dynamo_config_status(self, payload: dict) -> None:
        # Expect: { status, data: { samplingRate, emissionRate, ... } }
        try:
            if payload.get("status") == "success":
                data = payload.get("data") or {}
                cfg = {
                    "samplingRate": int(data.get("samplingRate") or 0),
                    "emissionRate": int(data.get("emissionRate") or 0),
                }
                if hasattr(self.view, "bridge"):
                    try:
                        self.view.bridge.dynamo_config_ready.emit(cfg)
                    except Exception:
                        pass
        except Exception:
            pass

    def _wakeup_backend(self) -> None:
        if self.client is None:
            return
        try:
            self.client.emit("getDynamoConfig")
        except Exception:
            pass
        try:
            self.client.emit("getGroups")
        except Exception:
            pass

    def _on_device_settings(self, payload: dict) -> None:
        # Expecting { status, data: DeviceSettings[] }
        data = payload.get("data") or []
        # Extract display name, id, and type code; tolerate different field casings
        devices: list[tuple[str, str, str]] = []
        for d in data:
            try:
                display_name = str(d.get("name") or "").strip() or str(d.get("axfId") or d.get("id") or "").strip()
                axf_id = str(d.get("axfId") or d.get("id") or "").strip()
                dev_type = str(d.get("deviceTypeId") or "").strip()
                if not axf_id or not dev_type:
                    continue
                if dev_type not in ("06", "07", "08"):
                    continue
                devices.append((display_name, axf_id, dev_type))
            except Exception:
                continue
        # Push to UI via bridge
        try:
            if hasattr(self.view, "bridge"):
                self.view.bridge.available_devices_ready.emit(devices)
        except Exception:
            pass

    def _on_connected_device_list(self, payload: dict | list) -> None:
        """Fallback handler for 'connectedDeviceList' which may return a list of devices.

        Expected structure (example):
        [
          { name, devices: [ { sensors: [...], ... } ], axfId/id, deviceTypeId }
        ]
        We extract (display_name, axf_id, device_type) tuples.
        """
        try:
            raw = payload
            if isinstance(raw, dict):
                data_list = raw.get("data") or raw.get("devices") or []
            else:
                data_list = raw or []
        except Exception:
            data_list = []
        devices: list[tuple[str, str, str]] = []
        for d in data_list:
            try:
                display_name = str(d.get("name") or "").strip() or str(d.get("axfId") or d.get("id") or "").strip()
                axf_id = str(d.get("axfId") or d.get("id") or "").strip()
                dev_type = str(d.get("deviceTypeId") or d.get("type") or "").strip()
                if not axf_id:
                    continue
                # If type absent, try to infer from axfId prefix (e.g., '06.')
                if not dev_type and axf_id and len(axf_id) >= 2 and axf_id[0:2].isdigit():
                    dev_type = axf_id[0:2]
                if dev_type and dev_type not in ("06", "07", "08"):
                    # keep unknowns out of list for now
                    continue
                devices.append((display_name, axf_id, dev_type or "06"))
            except Exception:
                continue
        try:
            if hasattr(self.view, "bridge"):
                self.view.bridge.available_devices_ready.emit(devices)
        except Exception:
            pass

    def _on_device_types(self, _payload: dict) -> None:
        # Not used yet; could map friendly names
        return

    def _on_group_definitions(self, payload: object) -> None:
        """Handle group definitions response (dict with status or raw list)."""
        try:
            if isinstance(payload, dict):
                if payload.get("status") != "success":
                    try:
                        print(f"[ctrl] getGroupDefinitions failed: {payload.get('message')}")
                    except Exception:
                        pass
                    return
                data = payload.get("data") or []
            elif isinstance(payload, list):
                data = payload
            else:
                data = []
        except Exception:
            data = []
        for definition in data:
            try:
                name = definition.get("name")
                if name == "Pitching Mound":
                    self._mound_definition = definition
                    break
            except Exception:
                continue
        if self._mound_definition is None:
            try:
                print("[ctrl] WARNING: Pitching Mound definition not found in response!")
            except Exception:
                pass
    
    def set_mound_device(self, position_id: str, device_id: str) -> None:
        """Set device for a specific mound position and create/update group if all positions filled."""
        new_device_id = (device_id or "").strip()
        if not new_device_id:
            return

        previous_device_at_position = self._mound_selected_devices.get(position_id)

        # If selecting the same device for the same position, no-op
        if previous_device_at_position == new_device_id:
            return

        # If this device is already assigned to another position, perform an automatic swap
        other_position_using_new: Optional[str] = None
        for pos, dev in list(self._mound_selected_devices.items()):
            if pos != position_id and dev == new_device_id:
                other_position_using_new = pos
                break

        # Assign the new device to the requested position
        self._mound_selected_devices[position_id] = new_device_id

        # If another position was using this device, swap it with the previous device (if any)
        if other_position_using_new is not None:
            if previous_device_at_position:
                # Complete swap
                self._mound_selected_devices[other_position_using_new] = previous_device_at_position
                print(f"[ctrl] Swapped: {position_id} <- {new_device_id}, {other_position_using_new} <- {previous_device_at_position}")
            else:
                # No previous device at target position; clear the other position
                self._mound_selected_devices.pop(other_position_using_new, None)
                print(f"[ctrl] Moved device {new_device_id} from {other_position_using_new} to {position_id}; cleared {other_position_using_new}")

        print(f"[ctrl] Set {position_id} = {new_device_id}")
        
        # Check if all three positions are filled
        required_positions = ["Upper Landing Zone", "Lower Landing Zone", "Launch Zone"]
        if all(pos in self._mound_selected_devices for pos in required_positions):
            self._create_or_update_mound_group()
    
    def _create_or_update_mound_group(self) -> None:
        """Create or update the pitching mound device group."""
        if self._mound_definition is None or self.client is None:
            print(f"[ctrl] Cannot create mound group: definition={self._mound_definition is not None}, client={self.client is not None}")
            if self._mound_definition is None:
                print("[ctrl] Mound definition is None - did getGroupDefinitions succeed?")
            return
        
        required_positions = self._mound_definition.get("requiredGroupPositions") or []
        mappings = []
        
        for position_data in required_positions:
            position_id = position_data.get("positionId")
            mapping_index = position_data.get("mappingIndex")
            device_id = self._mound_selected_devices.get(position_id)
            
            if not device_id:
                continue
            
            mapping = {
                "positionId": position_id,
                "mappingIndex": mapping_index,
                "deviceId": device_id,
            }
            
            # If updating existing group, add configurationId and groupId
            if self._mound_group_id and self._mound_configuration_id:
                mapping["groupId"] = self._mound_group_id
                mapping["configurationId"] = self._mound_configuration_id
            
            mappings.append(mapping)
        
        if len(mappings) < 3:
            print(f"[ctrl] Not all positions mapped yet: {len(mappings)}/3")
            return
        
        # Create or update group
        if self._mound_group_id and self._mound_configuration_id:
            # Update existing group
            payload = {
                "axfId": self._mound_group_id,
                "groupDefinitionId": self._mound_definition.get("axfId"),
                "name": "Quick Mound Config",
                "disableVirtualDevices": False,
                "mappings": mappings,
            }
            print(f"[ctrl] Updating device group: {payload}")
            self.client.on("updateDeviceGroupStatus", self._on_device_group_status)
            self.client.emit("updateDeviceGroup", payload)
        else:
            # Create new group
            payload = {
                "groupDefinitionId": self._mound_definition.get("axfId"),
                "name": "Quick Mound Config",
                "disableVirtualDevices": False,
                "mappings": mappings,
            }
            print(f"[ctrl] Creating device group: {payload}")
            self.client.on("createDeviceGroupStatus", self._on_device_group_status)
            self.client.emit("createDeviceGroup", payload)
    
    def _on_device_group_status(self, payload: dict) -> None:
        """Handle create/update device group response."""
        if payload.get("status") == "success":
            group_data = payload.get("data")
            if group_data:
                self._mound_group_id = group_data.get("axfId")
                self._mound_configuration_id = group_data.get("configurationId")
                print(f"[ctrl] Device group success: groupId={self._mound_group_id}, configId={self._mound_configuration_id}")
        else:
            print(f"[ctrl] Device group failed: {payload.get('message')}")
    
    def _get_active_device_ids(self, timeout_seconds: float = 3.0) -> set[str]:
        """Return set of device IDs that have sent data within the timeout period."""
        current_time = time.time()
        active = set()
        with self._state_lock:
            for dev_id, last_seen in list(self._active_devices.items()):
                if current_time - last_seen <= timeout_seconds:
                    active.add(dev_id)
                # Clean up old entries
                elif current_time - last_seen > 10.0:
                    self._active_devices.pop(dev_id, None)
        return active

    def _tick_loop(self) -> None:
        # Throttled UI emissions; hz can change dynamically
        while not self._stop_flag.is_set():
            # Read current target Hz each frame to apply changes immediately
            try:
                hz = max(10, min(240, int(self._ui_tick_hz)))
            except Exception:
                hz = max(10, int(getattr(config, "UI_TICK_HZ", 60)))
            target_dt = 1.0 / float(hz)
            t0 = time.time()
            with self._state_lock:
                snaps = self.model.get_snapshot()
                # Reflect single snapshot if available
                single_snap = self._single_snapshot_latest
                data_hz_text = f"{self.model.ema_hz:.1f}" if getattr(self.model, "ema_hz", 0.0) else "--"
                ui_hz_text = f"{hz}"
                hz_text = f"Data Hz: {data_hz_text} | UI Hz: {ui_hz_text}"
            if hasattr(self.view, "bridge"):
                try:
                    self.view.bridge.snapshots_ready.emit(snaps, hz_text)
                    if single_snap is not None:
                        self.view.bridge.single_snapshot_ready.emit(single_snap)
                except Exception:
                    pass
            # Update connection status
            if self.client is not None and hasattr(self.view, "bridge"):
                txt = "Connected" if self.client.status.connected else "Reconnecting..." if self.client.status.last_error else "Connecting..."
                try:
                    self.view.bridge.connection_text_ready.emit(txt)
                except Exception:
                    pass
            
            # Update active devices indicator
            if hasattr(self.view, "bridge"):
                try:
                    active_ids = self._get_active_device_ids()
                    self.view.bridge.active_devices_ready.emit(active_ids)
                except Exception:
                    pass

            # Throttled force vector emission for Sensor View at ~UI tick
            try:
                fv: Optional[tuple[str, int, float, float, float]]
                with self._state_lock:
                    fv = self._pending_force_vector
                if fv and hasattr(self.view, "bridge"):
                    try:
                        dev_id, t_ms, fx, fy, fz = fv
                        self.view.bridge.force_vector_ready.emit(dev_id, t_ms, fx, fy, fz)
                    except Exception:
                        pass
            except Exception:
                pass

            # Mound-mode batched per-zone force emission at ~60 Hz
            try:
                if hasattr(self.view, "state") and getattr(self.view.state, "display_mode", "") == "mound":
                    snapshot: dict[str, tuple[int, float, float, float]]
                    with self._state_lock:
                        snapshot = dict(self._pending_mound_force_vectors)
                    if snapshot and hasattr(self.view, "bridge"):
                        try:
                            self.view.bridge.mound_force_vectors_ready.emit(snapshot)
                        except Exception:
                            pass
            except Exception:
                pass

            # Batched moments emission for Moments View at ~60 Hz
            try:
                moments_snapshot: dict[str, tuple[int, float, float, float]]
                with self._state_lock:
                    moments_snapshot = dict(self._pending_moment_vectors)
                if moments_snapshot and hasattr(self.view, "bridge"):
                    try:
                        self.view.bridge.moments_ready.emit(moments_snapshot)
                    except Exception:
                        pass
            except Exception:
                pass

            elapsed = time.time() - t0
            sleep_s = max(0.0, target_dt - elapsed)
            if sleep_s == 0:
                self._overrun_count += 1
                if self._overrun_count % 300 == 0:
                    try:
                        print(f"[ctrl] Warning: UI loop overruns detected: {self._overrun_count}")
                    except Exception:
                        pass
            else:
                # Reset counter on healthy frame
                if self._overrun_count:
                    self._overrun_count = 0
            time.sleep(sleep_s)

    def stop(self) -> None:
        self._stop_flag.set()
        self.disconnect()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)


