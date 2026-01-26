from __future__ import annotations

import copy
from typing import Dict, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from .. import config
from ..app_services.temperature_post_correction import apply_post_correction_to_run_data, compute_delta_t_f
from .bridge import UiBridge  # Keep for compatibility if needed by other components
from .controllers.main_controller import MainController
from .pane_switcher import PaneSwitcher
from .panels.control_panel import ControlPanel
from .state import ViewState
from .widgets.force_plot import ForcePlotWidget
from .widgets.moments_view import MomentsViewWidget
from .widgets.temp_coef_widget import TempCoefWidget
from .widgets.temp_plot_widget import TempPlotWidget
from .widgets.world_canvas import WorldCanvas


class FluxLitePage(QtWidgets.QWidget):
    """
    FluxLite tool UI as a QWidget, suitable for hosting inside FluxDeluxe.

    This contains all FluxLite-specific controllers, state, and UI wiring.
    """

    connection_status_changed = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)

        # Pane Switching Helper (internal to FluxLite tool)
        self.pane_switcher = PaneSwitcher()

        # Initialize Controller
        self.controller = MainController()

        # Initialize State (View Model)
        self.state = ViewState()

        # Track connection/streaming state for clean disconnect behavior
        self._connected_device_ids: set[str] = set()
        self._active_device_ids: set[str] = set()

        # Legacy Bridge (kept for compatibility)
        self.bridge = UiBridge()

        # UI Setup + wiring
        self._setup_ui()
        self._connect_signals()

        # Start Controller (triggers autoconnect)
        self.controller.start()

    def shutdown(self) -> None:
        """Cleanup and shutdown services."""
        try:
            self.controller.shutdown()
        except Exception:
            pass

    def _setup_ui(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        self.canvas_left = WorldCanvas(self.state, backend_address_provider=self.controller.hardware.backend_http_address)
        self.canvas_right = WorldCanvas(self.state, backend_address_provider=self.controller.hardware.backend_http_address)
        self.canvas = self.canvas_left  # Default active canvas

        # Control Panel (Bottom)
        self.controls = ControlPanel(self.state, self.controller)

        self.top_tabs_left = QtWidgets.QTabWidget()
        self.top_tabs_right = QtWidgets.QTabWidget()

        # Left Tabs
        self.top_tabs_left.addTab(self.canvas_left, "Plate View")

        sensor_left = QtWidgets.QWidget()
        sll = QtWidgets.QVBoxLayout(sensor_left)
        sll.setContentsMargins(0, 0, 0, 0)
        self.sensor_plot_left = ForcePlotWidget()
        sll.addWidget(self.sensor_plot_left)
        self.top_tabs_left.addTab(sensor_left, "Sensor View")

        moments_left = MomentsViewWidget()
        self.moments_view_left = moments_left
        self.top_tabs_left.addTab(moments_left, "Moments View")

        # Discrete Temp: Temp-vs-Force plot tab
        self.temp_plot_tab = TempPlotWidget(hardware_service=self.controller.hardware)
        self.top_tabs_left.addTab(self.temp_plot_tab, "Temp Plot")
        self.pane_switcher.register_tab(self.top_tabs_left, self.temp_plot_tab, "temp_plot")

        # Right Tabs
        self.top_tabs_right.addTab(self.canvas_right, "Plate View")
        self.pane_switcher.register_tab(self.top_tabs_right, self.canvas_right, "plate_view_right")

        sensor_right = QtWidgets.QWidget()
        srl = QtWidgets.QVBoxLayout(sensor_right)
        srl.setContentsMargins(0, 0, 0, 0)
        self.sensor_plot_right = ForcePlotWidget()
        srl.addWidget(self.sensor_plot_right)
        self.top_tabs_right.addTab(sensor_right, "Sensor View")

        moments_right = MomentsViewWidget()
        self.moments_view_right = moments_right
        self.top_tabs_right.addTab(moments_right, "Moments View")

        # Discrete Temp: coefficient metrics tab (no slope logic)
        self.temp_coef_tab = TempCoefWidget()
        self.top_tabs_right.addTab(self.temp_coef_tab, "Temp Coefs")
        self.pane_switcher.register_tab(self.top_tabs_right, self.temp_coef_tab, "temp_coefs")

        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.splitter.addWidget(self.top_tabs_left)
        self.splitter.addWidget(self.top_tabs_right)

        self.top_tabs_left.setMovable(True)
        self.top_tabs_right.setMovable(True)

        outer.addWidget(self.splitter, 1)
        outer.addWidget(self.controls)

        # Initial sizing
        self.splitter.setSizes([800, 800])

        # Initial pane layout: Left=Plate(0), Right=Sensor(1)
        self.top_tabs_left.setCurrentIndex(0)
        self.top_tabs_right.setCurrentIndex(1)

        # Clear canvases AND overlays
        self.canvas_left.clear_live_colors()
        self.canvas_right.clear_live_colors()
        self.canvas_left.hide_live_grid()  # Ensure overlay is hidden
        self.canvas_right.hide_live_grid()
        self.canvas_left.repaint()
        self.canvas_right.repaint()

        # Auto-scan devices on startup
        QtCore.QTimer.singleShot(1000, self.controller.hardware.fetch_discovery)

    def _connect_signals(self) -> None:
        # Hardware Signals
        try:
            self.controller.hardware.connection_status_changed.connect(self.connection_status_changed.emit)
        except Exception:
            pass

        # Data Signals
        self.controller.hardware.data_received.connect(self._on_live_data)

        # Connect Control Panel signals to Controller
        self.controls.connect_requested.connect(self.controller.hardware.connect)
        self.controls.disconnect_requested.connect(self.controller.hardware.disconnect)
        self.controls.start_capture_requested.connect(self.controller.hardware.start_capture)
        self.controls.stop_capture_requested.connect(self.controller.hardware.stop_capture)
        self.controls.tare_requested.connect(self.controller.hardware.tare)
        self.controls.refresh_devices_requested.connect(self.controller.hardware.fetch_discovery)

        # Backend Config Signals
        self.controls.backend_model_bypass_changed.connect(self.controller.models.set_bypass)
        self.controls.backend_temperature_apply_requested.connect(
            lambda p: self.controller.hardware.configure_temperature_correction(
                p.get("slopes", {}),
                p.get("use_temperature_correction", False),
                p.get("room_temperature_f", 72.0),
            )
        )

        # Data Sync
        self.controls.data_sync_requested.connect(self.controller.data_sync.sync_all)

        # Hardware -> UI Signals
        self.controller.hardware.device_list_updated.connect(self.controls.set_available_devices)
        self.controller.hardware.device_list_updated.connect(self._on_device_list_updated)
        self.controller.hardware.active_devices_updated.connect(self.controls.update_active_devices)
        self.controller.hardware.active_devices_updated.connect(self._auto_select_active_device)

        # When device/layout selection changes, re-fit the plate view so it returns
        # to the default framing (80% height/width target).
        try:
            self.controls.config_changed.connect(self._on_view_config_changed)
        except Exception:
            pass

        # Live Testing Signals - show grid on both canvases
        self.controller.live_test.view_grid_configured.connect(self.canvas_left.show_live_grid)
        self.controller.live_test.view_grid_configured.connect(self.canvas_right.show_live_grid)
        self.controller.live_test.view_session_ended.connect(self.canvas_left.hide_live_grid)
        self.controller.live_test.view_session_ended.connect(self.canvas_right.hide_live_grid)
        self.controller.live_test.view_cell_updated.connect(self._on_live_cell_updated)

        # User interaction: clicks on the live grid overlay (either canvas)
        self.canvas_left.live_cell_clicked.connect(self._on_live_cell_clicked)
        self.canvas_right.live_cell_clicked.connect(self._on_live_cell_clicked)

        # Plate quick actions (overlay buttons)
        try:
            self.canvas_left.refresh_devices_clicked.connect(self.controller.hardware.fetch_discovery)
            self.canvas_right.refresh_devices_clicked.connect(self.controller.hardware.fetch_discovery)
            # Tare doesn't require a group id; default to empty.
            self.canvas_left.tare_clicked.connect(lambda: self.controller.hardware.tare(""))
            self.canvas_right.tare_clicked.connect(lambda: self.controller.hardware.tare(""))
        except Exception:
            pass

        # Sync plate rotation between left/right views.
        try:
            self.canvas_left.rotation_changed.connect(self.canvas_right.set_rotation_quadrants)
            self.canvas_right.rotation_changed.connect(self.canvas_left.set_rotation_quadrants)
        except Exception:
            pass

        # Discrete Temp: wire test selection + plot button to Temp Plot/Slopes
        live_panel = self.controls.live_testing_panel
        live_panel.discrete_test_selected.connect(self.temp_plot_tab.set_test_path)
        live_panel.discrete_test_selected.connect(self._on_discrete_test_selected)  # Switch tabs on selection
        live_panel.plot_test_requested.connect(self.temp_plot_tab.plot_current)
        live_panel.process_test_requested.connect(self.temp_plot_tab.process_current)

        # Link Temp Plot <-> Coef metrics widget (toggle controls + computed values)
        try:
            self.temp_plot_tab.set_coef_widget(self.temp_coef_tab)
        except Exception:
            pass

        # Temp Testing Signals
        self._temp_analysis_payload: Optional[Dict] = None
        self._temp_analysis_payload_raw: Optional[Dict] = None
        self._temp_post_correction_enabled = False
        self._temp_post_correction_k = 0.0
        temp_panel = self.controls.temperature_testing_panel
        temp_ctrl = self.controller.temp_test
        try:
            enabled, k = temp_panel.post_correction_settings()
            self._temp_post_correction_enabled = bool(enabled)
            self._temp_post_correction_k = float(k or 0.0)
        except Exception:
            pass
        # Wire analysis results
        temp_ctrl.analysis_ready.connect(self._on_temp_analysis_ready)
        temp_ctrl.grid_display_ready.connect(self._on_temp_grid_display_ready)
        # Re-render when stage changes
        temp_panel.stage_changed.connect(self._on_temp_stage_changed)
        # Re-render when grading mode changes (Absolute vs Bias Controlled)
        temp_panel.grading_mode_changed.connect(self._on_temp_grading_mode_changed)
        # Re-render when post-processing correction settings change
        temp_panel.post_correction_changed.connect(self._on_temp_post_correction_changed)
        # Plot button - goes through controller, then back to main thread for matplotlib
        temp_panel.plot_stages_requested.connect(temp_ctrl.plot_stage_detection)
        temp_ctrl.plot_ready.connect(self._on_temp_plot_ready)

        # Populate the temperature testing device list on startup (no auto-selection).
        try:
            QtCore.QTimer.singleShot(250, temp_ctrl.refresh_devices)
        except Exception:
            pass

        # Heatmap / Calibration Signals
        cal_ctrl = self.controller.calibration
        live_panel.load_45v_requested.connect(self._on_load_calibration)
        live_panel.generate_heatmap_requested.connect(self._on_generate_heatmap)
        live_panel.heatmap_selected.connect(self._on_heatmap_selected)

        cal_ctrl.status_updated.connect(live_panel.set_calibration_status)
        cal_ctrl.files_loaded.connect(live_panel.set_generate_enabled)
        cal_ctrl.heatmap_ready.connect(self._on_heatmap_ready)

        # Model Management Signals
        model_svc = self.controller.models
        model_svc.metadata_received.connect(self._on_model_metadata_received)
        model_svc.activation_status_received.connect(self._on_model_activation_status)
        live_panel.activate_model_requested.connect(self._on_activate_model_requested)
        live_panel.deactivate_model_requested.connect(self._on_deactivate_model_requested)
        live_panel.package_model_requested.connect(self._on_package_model_requested)

        # Mound Device Mapping
        self.canvas_left.mound_device_selected.connect(self._on_mound_device_selected)
        self.canvas_right.mound_device_selected.connect(self._on_mound_device_selected)

    def _on_mound_device_selected(self, pos_id: str, dev_id: str) -> None:
        """Trigger update on both canvases when mound mapping changes."""
        self.canvas_left.update()
        self.canvas_right.update()

    def _on_live_data(self, payload: dict) -> None:
        """Handle live streaming data from the backend."""
        try:
            # Buffer raw payload for discrete temperature testing
            self.controller.testing.buffer_live_payload(payload)

            # Extract list of device frames
            frames = []
            if isinstance(payload, list):
                frames = payload
            elif isinstance(payload, dict):
                # Check for "sensors" list (raw data stream) vs "devices" list (processed stream)
                if "sensors" in payload and isinstance(payload["sensors"], list):
                    did = str(payload.get("deviceId") or "").strip()
                    if did:
                        sum_sensor = next((s for s in payload["sensors"] if s.get("name") == "Sum"), None)
                        if sum_sensor:
                            cop_data = payload.get("cop") or {}
                            frames.append(
                                {
                                    "id": did,
                                    "fx": float(sum_sensor.get("x", 0.0)),
                                    "fy": float(sum_sensor.get("y", 0.0)),
                                    "fz": float(sum_sensor.get("z", 0.0)),
                                    "time": payload.get("time"),
                                    "avgTemperatureF": payload.get("avgTemperatureF"),
                                    "cop": {"x": float(cop_data.get("x", 0.0)), "y": float(cop_data.get("y", 0.0))},
                                }
                            )
                elif "devices" in payload and isinstance(payload["devices"], list):
                    frames = payload["devices"]
                elif "id" in payload or "deviceId" in payload:
                    frames = [payload]

            # Find the "active" device selected in UI
            selected_id = (self.state.selected_device_id or "").strip()

            # Also support mound mode mapping
            mound_map = self.state.mound_devices if self.state.display_mode == "mound" else {}

            snapshots = {}  # For mound view
            moments_data = {}  # For moments view

            for frame in frames:
                did = str(frame.get("id") or frame.get("deviceId") or "").strip()
                if not did:
                    continue

                try:
                    fx = float(frame.get("fx", 0.0))
                    fy = float(frame.get("fy", 0.0))
                    fz = float(frame.get("fz", 0.0))
                    t_ms = int(frame.get("time") or frame.get("t") or 0)

                    # COP
                    cop = frame.get("cop") or {}
                    cop_x = float(cop.get("x", 0.0))
                    cop_y = float(cop.get("y", 0.0))

                    # Moments
                    moments = frame.get("moments") or {}
                    mx = float(moments.get("x", 0.0))
                    my = float(moments.get("y", 0.0))
                    mz = float(moments.get("z", 0.0))
                    moments_data[did] = (t_ms, mx, my, mz)

                    # Is this the selected device?
                    if self.state.display_mode == "single" and did == selected_id:
                        # 1. Update Sensor Plot (Right pane by default)
                        if self.sensor_plot_right:
                            self.sensor_plot_right.add_point(t_ms, fx, fy, fz)

                        # Update Temp Label (Left/Right Sensor Plot)
                        try:
                            avg_temp = float(frame.get("avgTemperatureF") or 0.0)
                            if avg_temp > 1.0:
                                if self.sensor_plot_left:
                                    self.sensor_plot_left.set_temperature_f(avg_temp)
                                if self.sensor_plot_right:
                                    self.sensor_plot_right.set_temperature_f(avg_temp)
                            else:
                                if self.sensor_plot_left:
                                    self.sensor_plot_left.set_temperature_f(None)
                                if self.sensor_plot_right:
                                    self.sensor_plot_right.set_temperature_f(None)
                        except Exception:
                            pass

                        # 2. Update Plate View (Left pane by default) - Single Snapshot
                        is_visible = abs(fz) > 5.0  # Basic threshold
                        snap = (cop_x, cop_y, fz, t_ms, is_visible, cop_x, cop_y)
                        self.canvas_left.set_single_snapshot(snap)
                        self.canvas_right.set_single_snapshot(snap)  # Sync if both showing plate

                    # Mound mapping
                    if self.state.display_mode == "mound":
                        for pos_name, mapped_id in mound_map.items():
                            if mapped_id == did:
                                is_visible = abs(fz) > 5.0
                                snap = (cop_x, cop_y, fz, t_ms, is_visible, cop_x, cop_y)
                                snapshots[pos_name] = snap
                                break

                except Exception:
                    continue

            if self.state.display_mode == "mound" and snapshots:
                self.canvas_left.set_snapshots(snapshots)
                self.canvas_right.set_snapshots(snapshots)

            if moments_data:
                try:
                    if self.moments_view_left:
                        self.moments_view_left.set_moments(moments_data)
                    if self.moments_view_right:
                        self.moments_view_right.set_moments(moments_data)
                except Exception:
                    pass

        except Exception:
            pass

    def _on_live_cell_updated(self, row, col, result) -> None:
        color = None
        text = None
        if isinstance(result, dict):
            color = result.get("color")
            text = result.get("text")

        if not isinstance(color, QtGui.QColor):
            color = QtGui.QColor(0, 255, 0, 100)  # Green default fallback

        self.canvas.set_live_cell_color(row, col, color)
        if text:
            self.canvas.set_live_cell_text(row, col, text)

    def _on_live_cell_clicked(self, row: int, col: int) -> None:
        """Bridge canvas cell clicks into the live-test controller."""
        try:
            self.controller.live_test.handle_cell_click(int(row), int(col), {})
        except Exception:
            pass

    def _on_discrete_test_selected(self, path: str) -> None:
        """Switch to Temp Plot tab when a discrete test is selected."""
        if not path:
            return
        try:
            self.pane_switcher.switch_many("temp_plot", "temp_coefs")
        except Exception:
            pass

    def _auto_select_active_device(self, active_device_ids: set) -> None:
        """
        When a device is actively streaming, auto-select it so Plate View + Sensor View show data.
        """
        try:
            self._active_device_ids = set(str(x) for x in (active_device_ids or set()) if str(x).strip())
            active = sorted(self._active_device_ids)
            if not active:
                if not self._connected_device_ids:
                    self._clear_device_views()
                return

            selected = str(self.state.selected_device_id or "").strip()
            should_select = (not selected) or (selected not in active and len(active) == 1)
            if not should_select:
                return

            target_id = active[0]

            # Select in the Config device list so we go through the normal wiring.
            try:
                lw = getattr(self.controls, "device_list", None)
                if lw is None:
                    self.state.selected_device_id = target_id
                    self.state.display_mode = "single"
                    self.canvas_left.update()
                    self.canvas_right.update()
                    return

                for i in range(lw.count()):
                    item = lw.item(i)
                    if item is None:
                        continue
                    try:
                        _name, axf_id, _dev_type = item.data(QtCore.Qt.UserRole)
                    except Exception:
                        continue
                    if str(axf_id).strip() == target_id:
                        lw.setCurrentItem(item)
                        break
            except Exception:
                self.state.selected_device_id = target_id
                self.state.display_mode = "single"
                try:
                    self.canvas_left.invalidate_fit()
                    self.canvas_right.invalidate_fit()
                except Exception:
                    self.canvas_left.update()
                    self.canvas_right.update()
        except Exception:
            pass

    def _on_device_list_updated(self, devices: list) -> None:
        """
        Maintain a cached set of connected device IDs from connectedDeviceList.
        When both connected + active are empty, revert to the "No Devices Connected" UI.
        """
        try:
            ids: set[str] = set()
            for d in (devices or []):
                try:
                    _name, axf_id, _dt = d
                    if axf_id:
                        ids.add(str(axf_id).strip())
                except Exception:
                    continue
            self._connected_device_ids = ids
            if not self._connected_device_ids and not self._active_device_ids:
                self._clear_device_views()
        except Exception:
            pass

    def _clear_device_views(self) -> None:
        """Revert UI back to the empty-state plate and clear sensor plots."""
        try:
            # Clear selection state
            self.state.selected_device_id = None
            self.state.selected_device_type = None
            self.state.selected_device_name = None
            self.state.display_mode = "single"

            # Clear config list selection (avoid firing selection handlers)
            try:
                lw = getattr(self.controls, "device_list", None)
                if lw is not None:
                    lw.blockSignals(True)
                    lw.setCurrentRow(-1)
                    lw.blockSignals(False)
            except Exception:
                pass

            # Clear plate visuals
            try:
                self.canvas_left.hide_live_grid()
                self.canvas_right.hide_live_grid()
            except Exception:
                pass
            try:
                self.canvas_left.clear_live_colors()
                self.canvas_right.clear_live_colors()
            except Exception:
                pass
            try:
                self.canvas_left.set_heatmap_points([])
                self.canvas_right.set_heatmap_points([])
            except Exception:
                pass
            try:
                self.canvas_left.set_single_snapshot(None)
                self.canvas_right.set_single_snapshot(None)
            except Exception:
                pass
            try:
                self.canvas_left.repaint()
                self.canvas_right.repaint()
            except Exception:
                pass
            try:
                self.canvas_left.invalidate_fit()
                self.canvas_right.invalidate_fit()
            except Exception:
                pass

            # Clear sensor plots
            try:
                if self.sensor_plot_left:
                    self.sensor_plot_left.clear()
                    self.sensor_plot_left.set_temperature_f(None)
                if self.sensor_plot_right:
                    self.sensor_plot_right.clear()
                    self.sensor_plot_right.set_temperature_f(None)
            except Exception:
                pass
        except Exception:
            pass

    def _on_view_config_changed(self) -> None:
        """Re-apply default plate framing when selection/layout changes."""
        try:
            self.canvas_left.invalidate_fit()
            self.canvas_right.invalidate_fit()
        except Exception:
            try:
                self.canvas_left.update()
                self.canvas_right.update()
            except Exception:
                pass

        # Update Live Testing panel's device info from current selection
        try:
            device_id = (self.state.selected_device_id or "").strip()
            device_type = (self.state.selected_device_type or "").strip()
            device_name = (self.state.selected_device_name or "").strip()

            live_panel = self.controls.live_testing_panel

            # Update device label (show device ID or name)
            display_device = device_id or device_name or ""
            live_panel.lbl_device.setText(display_device or "—")

            # Update model label with device type (plate type)
            live_panel.lbl_model.setText(device_type or "—")

            # Request model metadata for this device so we can populate the model list
            if device_id:
                # Show loading state while fetching
                live_panel.set_current_model("Loading...")
                live_panel.set_model_status("Fetching models...")
                live_panel.set_model_controls_enabled(False)
                self.controller.models.request_metadata(device_id)
            else:
                # No device selected - clear model info
                live_panel.set_current_model("—")
                live_panel.set_model_list([])
                live_panel.set_model_status("")
        except Exception:
            pass

    def _on_load_calibration(self) -> None:
        try:
            d = QtWidgets.QFileDialog(self)
            d.setFileMode(QtWidgets.QFileDialog.Directory)
            d.setOption(QtWidgets.QFileDialog.ShowDirsOnly, True)
            if d.exec():
                dirs = d.selectedFiles()
                if dirs:
                    self.controller.calibration.load_folder(dirs[0])
        except Exception:
            pass

    def _on_generate_heatmap(self) -> None:
        try:
            # Clear previous results
            self.controls.live_testing_panel.clear_heatmap_entries()
            self._heatmaps = {}

            model_id = (self.state.selected_device_id or "06").strip()
            plate_type = (self.state.selected_device_type or "06").strip()
            device_id = (self.state.selected_device_id or "").strip()

            self.controller.calibration.generate_heatmaps(model_id, plate_type, device_id)
        except Exception:
            pass

    def _on_heatmap_ready(self, tag: str, data: dict) -> None:
        try:
            if not hasattr(self, "_heatmaps"):
                self._heatmaps = {}
            self._heatmaps[tag] = data

            # Add to list widget in UI
            count = int((data.get("metrics") or {}).get("count") or 0)
            self.controls.live_testing_panel.add_heatmap_entry(tag, tag, count)
        except Exception:
            pass

    def _on_heatmap_selected(self, key: str) -> None:
        try:
            data = (getattr(self, "_heatmaps", {}) or {}).get(key)
            if not data:
                return

            # Update metrics table
            metrics = data.get("metrics") or {}
            self.controls.live_testing_panel.set_heatmap_metrics(metrics, False)

            # Update canvas
            points = data.get("points") or []
            tuples = []
            for p in points:
                tuples.append((float(p.get("x_mm", 0)), float(p.get("y_mm", 0)), str(p.get("bin", "green"))))

            self.canvas_left.set_heatmap_points(tuples)
            self.canvas_right.set_heatmap_points(tuples)
            self.canvas_left.repaint()
            self.canvas_right.repaint()
        except Exception:
            pass

    def _on_temp_analysis_ready(self, payload: dict) -> None:
        """Handle temperature analysis results."""
        self._temp_analysis_payload_raw = payload
        corrected = self._build_temp_post_correction_payload(
            payload,
            enabled=self._temp_post_correction_enabled,
            k=self._temp_post_correction_k,
        )
        self._render_temp_analysis_payload(corrected)

    def _on_temp_post_correction_changed(self, enabled: bool, k: float) -> None:
        self._temp_post_correction_enabled = bool(enabled)
        try:
            self._temp_post_correction_k = float(k or 0.0)
        except Exception:
            self._temp_post_correction_k = 0.0
        self._apply_temp_post_correction()

    def _apply_temp_post_correction(self) -> None:
        if not self._temp_analysis_payload_raw:
            return
        corrected = self._build_temp_post_correction_payload(
            self._temp_analysis_payload_raw,
            enabled=self._temp_post_correction_enabled,
            k=self._temp_post_correction_k,
        )
        self._render_temp_analysis_payload(corrected)

    def _render_temp_analysis_payload(self, payload: dict) -> None:
        self._temp_analysis_payload = payload
        # Update metrics panel
        try:
            grid = dict((payload or {}).get("grid") or {})
            meta = dict((payload or {}).get("meta") or {})
            self.controls.temperature_testing_panel.set_analysis_metrics(
                payload,
                device_type=str(grid.get("device_type", "06")),
                body_weight_n=float(meta.get("body_weight_n") or 0.0),
                bias_cache=self.controller.temp_test.bias_cache(),
                bias_map_all=self.controller.temp_test.bias_map(),
                grading_mode=self.controller.temp_test.grading_mode(),
            )
        except Exception:
            pass
        self._request_temp_grid_update()

    def _build_temp_post_correction_payload(self, payload: dict, *, enabled: bool, k: float) -> dict:
        if not payload or not enabled:
            return payload
        try:
            k = float(k or 0.0)
        except Exception:
            k = 0.0
        if k <= 0.0:
            return payload

        meta = dict((payload or {}).get("meta") or {})
        delta_t = compute_delta_t_f(meta=meta, ideal_room_temp_f=float(getattr(config, "TEMP_IDEAL_ROOM_TEMP_F", 76.0)))
        if delta_t is None:
            return payload
        if abs(delta_t) <= 1e-9:
            return payload

        corrected = copy.deepcopy(payload)
        try:
            apply_post_correction_to_run_data(
                corrected.get("selected") or {},
                delta_t_f=float(delta_t),
                k=float(k),
                fref_n=float(getattr(config, "TEMP_POST_CORRECTION_FREF_N", 550.0)),
            )
        except Exception:
            return payload
        return corrected

    def _on_temp_stage_changed(self, stage: str) -> None:
        if self._temp_analysis_payload:
            try:
                grid = dict((self._temp_analysis_payload or {}).get("grid") or {})
                meta = dict((self._temp_analysis_payload or {}).get("meta") or {})
                self.controls.temperature_testing_panel.set_analysis_metrics(
                    self._temp_analysis_payload,
                    device_type=str(grid.get("device_type", "06")),
                    body_weight_n=float(meta.get("body_weight_n") or 0.0),
                    bias_cache=self.controller.temp_test.bias_cache(),
                    bias_map_all=self.controller.temp_test.bias_map(),
                    grading_mode=self.controller.temp_test.grading_mode(),
                )
            except Exception:
                pass
            self._request_temp_grid_update()

    def _on_temp_grading_mode_changed(self, mode: str) -> None:
        try:
            self.controller.temp_test.set_grading_mode(mode)
        except Exception:
            pass
        if self._temp_analysis_payload:
            try:
                grid = dict((self._temp_analysis_payload or {}).get("grid") or {})
                meta = dict((self._temp_analysis_payload or {}).get("meta") or {})
                self.controls.temperature_testing_panel.set_analysis_metrics(
                    self._temp_analysis_payload,
                    device_type=str(grid.get("device_type", "06")),
                    body_weight_n=float(meta.get("body_weight_n") or 0.0),
                    bias_cache=self.controller.temp_test.bias_cache(),
                    bias_map_all=self.controller.temp_test.bias_map(),
                    grading_mode=self.controller.temp_test.grading_mode(),
                )
            except Exception:
                pass
            self._request_temp_grid_update()

    def _request_temp_grid_update(self) -> None:
        if not self._temp_analysis_payload:
            return
        try:
            stage_key = self.controls.temperature_testing_panel.current_stage()
        except Exception:
            stage_key = "All"
        self.controller.temp_test.prepare_grid_display(self._temp_analysis_payload, stage_key)

    def _on_temp_grid_display_ready(self, display_data: dict) -> None:
        """Apply prepared grid display data to canvases."""
        grid_info = display_data.get("grid_info", {})
        rows = int(grid_info.get("rows", 3))
        cols = int(grid_info.get("cols", 3))
        device_type = str(grid_info.get("device_type", "06"))
        device_id = str(display_data.get("device_id") or "")

        # Configure state for canvas rendering
        self.state.display_mode = "single"
        self.state.selected_device_type = device_type
        self.state.selected_device_id = device_id

        # Setup and clear canvases
        self.canvas_left.repaint()
        self.canvas_right.repaint()
        self.canvas_left.show_live_grid(rows, cols)
        self.canvas_right.show_live_grid(rows, cols)
        self.canvas_left.clear_live_colors()
        self.canvas_right.clear_live_colors()
        self.canvas_left.repaint()
        self.canvas_right.repaint()

        # Apply cells to canvases
        self._apply_cells_to_canvas(self.canvas_left, display_data.get("baseline_cells", []))
        self._apply_cells_to_canvas(self.canvas_right, display_data.get("selected_cells", []))

    def _apply_cells_to_canvas(self, canvas: WorldCanvas, cells: list) -> None:
        for cell in cells:
            row = int(cell.get("row", 0))
            col = int(cell.get("col", 0))
            text = str(cell.get("text", ""))

            color = cell.get("color")
            if not isinstance(color, QtGui.QColor):
                color_bin = str(cell.get("color_bin", "green"))
                rgba = config.COLOR_BIN_RGBA.get(color_bin, (0, 200, 0, 180))
                color = QtGui.QColor(*rgba)

            canvas.set_live_cell_color(row, col, color)
            canvas.set_live_cell_text(row, col, text)

    def _on_temp_plot_ready(self, data: dict) -> None:
        """Launch matplotlib plot on main thread."""
        try:
            from .widgets.temp_stage_plotter import plot_stage_comparison

            plot_stage_comparison(
                data.get("baseline_path", ""),
                data.get("selected_path", ""),
                data.get("body_weight_n", 800.0),
                baseline_windows=data.get("baseline_windows"),
                baseline_segments=data.get("baseline_segments"),
                selected_windows=data.get("selected_windows"),
                selected_segments=data.get("selected_segments"),
            )
        except Exception as e:
            print(f"[FluxLitePage] Plot error: {e}")

    # --- Model Management Handlers ---

    def _on_model_metadata_received(self, models: list) -> None:
        """Handle model metadata from backend - update Live Testing panel's model list."""
        try:
            print(f"[FluxLitePage] Model metadata received: {models}")
            live_panel = self.controls.live_testing_panel
            live_panel.set_model_list(models or [])
            live_panel.set_model_controls_enabled(True)

            # If there's an active model, update the current model display
            # Check various field names that backends might use
            active_model = None
            for m in (models or []):
                if not isinstance(m, dict):
                    continue
                # Check various ways the backend might indicate an active model
                is_active = (
                    m.get("modelActive") or  # Backend uses modelActive
                    m.get("isActive") or
                    m.get("active") or
                    m.get("is_active") or
                    str(m.get("status", "")).lower() == "active"
                )
                if is_active:
                    active_model = m.get("modelId") or m.get("model_id") or m.get("id") or m.get("name")
                    print(f"[FluxLitePage] Found active model: {active_model}")
                    break

            # Don't use first model as fallback - only show active models
            if not active_model:
                print("[FluxLitePage] No model is currently active")

            if active_model:
                print(f"[FluxLitePage] Setting current model to: {active_model}")
                live_panel.set_current_model(str(active_model))
                live_panel.set_session_model_id(str(active_model))
            else:
                print("[FluxLitePage] No active model found")
                live_panel.set_current_model("No active model")

            live_panel.set_model_status(None)  # Clear any loading status
        except Exception as e:
            print(f"[FluxLitePage] Model metadata error: {e}")

    def _on_model_activation_status(self, status: dict) -> None:
        """Handle model activation/deactivation status from backend."""
        try:
            print(f"[FluxLitePage] Model activation status received: {status}")
            live_panel = self.controls.live_testing_panel

            # Handle various response formats from backend
            success = bool(status.get("success", False) or status.get("ok", False))
            action = str(status.get("action") or status.get("type") or "").lower()
            model_id = str(status.get("modelId") or status.get("model_id") or status.get("activeModel") or "")
            error = str(status.get("error") or status.get("message") or "")

            # Some backends just return the active model ID directly
            active_model = status.get("activeModel") or status.get("activeModelId") or status.get("currentModel")

            if success or active_model is not None:
                if active_model:
                    # Backend told us directly which model is now active
                    live_panel.set_current_model(str(active_model))
                    live_panel.set_session_model_id(str(active_model))
                    live_panel.set_model_status(f"Active: {active_model}")
                elif action == "activate" and model_id:
                    live_panel.set_current_model(model_id)
                    live_panel.set_session_model_id(model_id)
                    live_panel.set_model_status(f"Activated: {model_id}")
                elif action == "deactivate" or active_model == "" or active_model is None:
                    live_panel.set_current_model("No active model")
                    live_panel.set_session_model_id("")
                    live_panel.set_model_status("Model deactivated")
                else:
                    live_panel.set_model_status("Success")
                # Note: Reconnect hint is shown when button is clicked, not here
            else:
                live_panel.set_model_status(f"Error: {error}" if error else "Operation failed")

            live_panel.set_model_controls_enabled(True)

            # Refresh metadata to get updated list and confirm active state
            device_id = (self.state.selected_device_id or "").strip()
            if device_id:
                # Small delay to let backend update its state
                QtCore.QTimer.singleShot(200, lambda: self.controller.models.request_metadata(device_id))
        except Exception as e:
            print(f"[FluxLitePage] Model activation status error: {e}")

    def _on_activate_model_requested(self, model_id: str) -> None:
        """Handle request to activate a model from the UI."""
        try:
            device_id = (self.state.selected_device_id or "").strip()
            print(f"[FluxLitePage] Activate model requested: device={device_id}, model={model_id}")
            if not device_id:
                self.controls.live_testing_panel.set_model_status("No device selected")
                self.controls.live_testing_panel.set_model_controls_enabled(True)
                return
            if not model_id:
                self.controls.live_testing_panel.set_model_status("No model selected")
                self.controls.live_testing_panel.set_model_controls_enabled(True)
                return

            self.controller.models.activate_model(device_id, model_id)
        except Exception as e:
            print(f"[FluxLitePage] Activate model error: {e}")
            try:
                self.controls.live_testing_panel.set_model_status(f"Error: {e}")
                self.controls.live_testing_panel.set_model_controls_enabled(True)
            except Exception:
                pass

    def _on_deactivate_model_requested(self, model_id: str) -> None:
        """Handle request to deactivate a model from the UI."""
        try:
            device_id = (self.state.selected_device_id or "").strip()
            print(f"[FluxLitePage] Deactivate model requested: device={device_id}, model={model_id}")
            if not device_id:
                self.controls.live_testing_panel.set_model_status("No device selected")
                self.controls.live_testing_panel.set_model_controls_enabled(True)
                return
            if not model_id:
                self.controls.live_testing_panel.set_model_status("No model to deactivate")
                self.controls.live_testing_panel.set_model_controls_enabled(True)
                return

            self.controller.models.deactivate_model(device_id, model_id)
        except Exception as e:
            print(f"[FluxLitePage] Deactivate model error: {e}")
            try:
                self.controls.live_testing_panel.set_model_status(f"Error: {e}")
                self.controls.live_testing_panel.set_model_controls_enabled(True)
            except Exception:
                pass

    def _on_package_model_requested(self) -> None:
        """Handle request to package a model from the UI."""
        try:
            from .dialogs.model_packager import ModelPackagerDialog

            dialog = ModelPackagerDialog(self)
            if dialog.exec() != QtWidgets.QDialog.Accepted:
                return

            force_dir, moments_dir, output_dir = dialog.get_values()
            if not force_dir or not output_dir:
                return

            self.controls.live_testing_panel.set_model_status("Packaging model...")
            self.controller.models.package_model(force_dir, moments_dir or "", output_dir)
        except Exception as e:
            print(f"[FluxLitePage] Package model error: {e}")
            try:
                self.controls.live_testing_panel.set_model_status(f"Error: {e}")
            except Exception:
                pass

