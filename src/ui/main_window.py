from __future__ import annotations

from typing import Callable, Dict, Optional, Tuple

from PySide6 import QtCore, QtWidgets

from .. import config
from .bridge import UiBridge
from .state import ViewState
from .widgets.world_canvas import WorldCanvas
from .panels.control_panel import ControlPanel
from .widgets.force_plot import ForcePlotWidget
from .widgets.moments_view import MomentsViewWidget


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AxioforceFluxLite")

        self.bridge = UiBridge()
        self.state = ViewState()
        self.canvas_left = WorldCanvas(self.state)
        self.canvas_right = WorldCanvas(self.state)
        self.canvas = self.canvas_left
        self.controls = ControlPanel(self.state)

        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.top_tabs_left = QtWidgets.QTabWidget()
        self.top_tabs_right = QtWidgets.QTabWidget()

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
        

        self.top_tabs_right.addTab(self.canvas_right, "Plate View")
        sensor_right = QtWidgets.QWidget()
        srl = QtWidgets.QVBoxLayout(sensor_right)
        srl.setContentsMargins(0, 0, 0, 0)
        self.sensor_plot_right = ForcePlotWidget()
        srl.addWidget(self.sensor_plot_right)
        self.top_tabs_right.addTab(sensor_right, "Sensor View")
        moments_right = MomentsViewWidget()
        self.moments_view_right = moments_right
        self.top_tabs_right.addTab(moments_right, "Moments View")
        self.top_tabs_right.setCurrentWidget(sensor_right)

        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.splitter.addWidget(self.top_tabs_left)
        self.splitter.addWidget(self.top_tabs_right)

        self.top_tabs_left.setMovable(True)
        self.top_tabs_right.setMovable(True)
        layout.addWidget(self.splitter)
        layout.addWidget(self.controls)
        self.controls.setMinimumHeight(220)
        layout.setStretch(0, 3)
        layout.setStretch(1, 2)
        self.top_tabs_left.setCurrentWidget(self.canvas_left)
        try:
            self.splitter.setStretchFactor(0, 1)
            self.splitter.setStretchFactor(1, 1)
            QtCore.QTimer.singleShot(0, lambda: self.splitter.setSizes([800, 800]))
        except Exception:
            pass
        self.setCentralWidget(central)

        self.status_label = QtWidgets.QLabel("Disconnected")
        self.rate_label = QtWidgets.QLabel("Hz: --")
        self.statusBar().addPermanentWidget(self.status_label)
        self.statusBar().addPermanentWidget(self.rate_label)

        self.controls.config_changed.connect(self._on_config_changed)
        self.controls.refresh_devices_requested.connect(self._on_refresh_devices)
        self.canvas_left.mound_device_selected.connect(self._on_mound_device_selected)
        self.canvas_right.mound_device_selected.connect(self._on_mound_device_selected)
        QtCore.QTimer.singleShot(500, lambda: self.controls.refresh_devices_requested.emit())

        self.bridge.snapshots_ready.connect(self.on_snapshots)
        self.bridge.connection_text_ready.connect(self.set_connection_text)
        self.bridge.single_snapshot_ready.connect(self.canvas_left.set_single_snapshot)
        self.bridge.single_snapshot_ready.connect(self.canvas_right.set_single_snapshot)
        self.bridge.plate_device_id_ready.connect(self.set_plate_device_id)
        self.bridge.available_devices_ready.connect(self.set_available_devices)
        self.bridge.active_devices_ready.connect(self.update_active_devices)
        self.bridge.force_vector_ready.connect(self._on_force_vector)
        self.bridge.moments_ready.connect(self._on_moments_ready)
        self.bridge.mound_force_vectors_ready.connect(self._on_mound_force_vectors)

    def on_snapshots(self, snaps: Dict[str, Tuple[float, float, float, int, bool, float, float]], hz_text: Optional[str]) -> None:
        if hz_text:
            self.rate_label.setText(hz_text)
        self.canvas_left.set_snapshots(snaps)
        self.canvas_right.set_snapshots(snaps)

    def set_connection_text(self, txt: str) -> None:
        self.status_label.setText(txt)

    def on_connect_clicked(self, slot: Callable[[str, int], None]) -> None:
        self.controls.connect_requested.connect(lambda h, p: slot(h, p))

    def on_disconnect_clicked(self, slot: Callable[[], None]) -> None:
        self.controls.disconnect_requested.connect(slot)

    def on_flags_changed(self, slot: Callable[[], None]) -> None:
        self.controls.flags_changed.connect(slot)

    def on_start_capture(self, slot: Callable[[dict], None]) -> None:
        self.controls.start_capture_requested.connect(lambda payload: slot(payload))

    def on_stop_capture(self, slot: Callable[[dict], None]) -> None:
        self.controls.stop_capture_requested.connect(lambda payload: slot(payload))

    def on_tare(self, slot: Callable[[str], None]) -> None:
        self.controls.tare_requested.connect(lambda gid: slot(gid))

    def on_config_changed(self, slot: Callable[[], None]) -> None:
        self.controls.config_changed.connect(slot)

    def set_available_devices(self, devices: List[Tuple[str, str]]) -> None:
        self.controls.set_available_devices(devices)
        self.canvas_left.set_available_devices(devices)
        self.canvas_right.set_available_devices(devices)

    def update_active_devices(self, active_device_ids: set) -> None:
        self.controls.update_active_devices(active_device_ids)
        self.canvas_left.update_active_devices(active_device_ids)
        self.canvas_right.update_active_devices(active_device_ids)

    def _on_config_changed(self) -> None:
        self.canvas_left._fit_done = False
        self.canvas_right._fit_done = False
        self.canvas_left.update()
        self.canvas_right.update()
        try:
            self.sensor_plot_left.clear()
            self.sensor_plot_right.clear()
            # Show dual-series legend only in mound mode
            is_mound = getattr(self, "state", None) is not None and getattr(self.state, "display_mode", "") == "mound"
            self.sensor_plot_left.set_dual_series_enabled(bool(is_mound))
            self.sensor_plot_right.set_dual_series_enabled(bool(is_mound))
        except Exception:
            pass

    def _on_mound_device_selected(self, position_id: str, device_id: str) -> None:
        if hasattr(self, "_on_mound_device_cb") and callable(self._on_mound_device_cb):
            try:
                self._on_mound_device_cb(position_id, device_id)
            except Exception:
                pass

    def on_mound_device_selected(self, slot: Callable[[str, str], None]) -> None:
        self._on_mound_device_cb = slot

    def on_request_discovery(self, slot: Callable[[], None]) -> None:
        self._on_refresh_cb = slot

    def _on_refresh_devices(self) -> None:
        try:
            if hasattr(self, "_on_refresh_cb") and callable(self._on_refresh_cb):
                self._on_refresh_cb()
        except Exception:
            pass

    def set_plate_device_id(self, plate_name: str, device_id: str) -> None:
        self.state.plate_device_ids[plate_name] = device_id

    def _on_force_vector(self, device_id: str, t_ms: int, fx: float, fy: float, fz: float) -> None:
        try:
            # In single-device mode, controller already filters to selected device
            if hasattr(self, "sensor_plot_left") and self.sensor_plot_left is not None:
                self.sensor_plot_left.add_point(t_ms, fx, fy, fz)
            if hasattr(self, "sensor_plot_right") and self.sensor_plot_right is not None:
                self.sensor_plot_right.add_point(t_ms, fx, fy, fz)
        except Exception:
            pass

    def _on_moments_ready(self, moments: dict) -> None:
        try:
            if hasattr(self, "moments_view_left") and self.moments_view_left is not None:
                self.moments_view_left.set_moments(moments)
            if hasattr(self, "moments_view_right") and self.moments_view_right is not None:
                self.moments_view_right.set_moments(moments)
        except Exception:
            pass

    def _on_mound_force_vectors(self, per_zone: dict) -> None:
        """per_zone: { 'Launch Zone': (t_ms, fx, fy, fz), 'Landing Zone': (t_ms, fx, fy, fz), ... }"""
        try:
            if hasattr(self, "sensor_plot_left") and self.sensor_plot_left is not None:
                self.sensor_plot_left.set_dual_series_enabled(True)
                if "Launch Zone" in per_zone:
                    t_ms, fx, fy, fz = per_zone.get("Launch Zone")
                    self.sensor_plot_left.add_point_launch(t_ms, fx, fy, fz)
                if "Landing Zone" in per_zone:
                    t_ms, fx, fy, fz = per_zone.get("Landing Zone")
                    self.sensor_plot_left.add_point_landing(t_ms, fx, fy, fz)
            if hasattr(self, "sensor_plot_right") and self.sensor_plot_right is not None:
                self.sensor_plot_right.set_dual_series_enabled(True)
                if "Launch Zone" in per_zone:
                    t_ms, fx, fy, fz = per_zone.get("Launch Zone")
                    self.sensor_plot_right.add_point_launch(t_ms, fx, fy, fz)
                if "Landing Zone" in per_zone:
                    t_ms, fx, fy, fz = per_zone.get("Landing Zone")
                    self.sensor_plot_right.add_point_landing(t_ms, fx, fy, fz)
        except Exception:
            pass

