from __future__ import annotations
from typing import Callable, Dict, Optional, Tuple, List
import os

from PySide6 import QtCore, QtWidgets, QtGui

from .. import config
from .controllers.main_controller import MainController
from .state import ViewState
from .widgets.world_canvas import WorldCanvas
from .panels.control_panel import ControlPanel
from .widgets.force_plot import ForcePlotWidget
from .widgets.moments_view import MomentsViewWidget
from .bridge import UiBridge # Keep for compatibility if needed by other components

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AxioforceFluxLite (Refactored)")

        # Initialize Controller
        self.controller = MainController()
        
        # Initialize State (View Model)
        self.state = ViewState()
        
        # Initialize Legacy Bridge (for compatibility, if needed)
        self.bridge = UiBridge()

        # UI Setup
        self._setup_ui()
        
        # Connect Signals
        self._connect_signals()
        
    def _setup_ui(self):
        self.canvas_left = WorldCanvas(self.state)
        self.canvas_right = WorldCanvas(self.state)
        self.canvas = self.canvas_left # Default active canvas
        
        # Control Panel (Left Side)
        self.controls = ControlPanel(self.state, self.controller)

        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

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

        # Right Tabs
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

        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.splitter.addWidget(self.top_tabs_left)
        self.splitter.addWidget(self.top_tabs_right)

        self.top_tabs_left.setMovable(True)
        self.top_tabs_right.setMovable(True)
        
        layout.addWidget(self.splitter)
        layout.addWidget(self.controls)

        # Layout stretching
        layout.setStretch(0, 3)
        layout.setStretch(1, 2)

        self.setCentralWidget(central)

        self.status_label = QtWidgets.QLabel("Disconnected")
        self.statusBar().addPermanentWidget(self.status_label)
        
        # Initial sizing
        self.splitter.setSizes([800, 800])

    def _connect_signals(self):
        # Hardware Signals
        self.controller.hardware.connection_status_changed.connect(self.status_label.setText)
        
        # Data Signals
        # Note: We need to adapt the raw data dictionary to what WorldCanvas expects
        # or update WorldCanvas to accept the new format.
        # For now, we might need a small adapter in MainController or here.
        
        # Connect Control Panel signals to Controller
        self.controls.connect_requested.connect(self.controller.hardware.connect)
        self.controls.disconnect_requested.connect(self.controller.hardware.disconnect)
        self.controls.start_capture_requested.connect(self.controller.hardware.start_capture)
        self.controls.stop_capture_requested.connect(self.controller.hardware.stop_capture)
        self.controls.tare_requested.connect(self.controller.hardware.tare)
        
        # Backend Config Signals
        self.controls.backend_model_bypass_changed.connect(self.controller.models.set_bypass)
        self.controls.backend_temperature_apply_requested.connect(
            lambda p: self.controller.hardware.configure_temperature_correction(
                p.get("slopes", {}), 
                p.get("use_temperature_correction", False), 
                p.get("room_temperature_f", 72.0)
            )
        )
        
        # Data Sync
        self.controls.data_sync_requested.connect(self.controller.data_sync.sync_all)
        
        # Hardware -> UI Signals
        self.controller.hardware.device_list_updated.connect(self.controls.set_available_devices)
        
        # Live Testing Signals
        self.controller.live_test.view_grid_configured.connect(self.canvas.show_live_grid)
        self.controller.live_test.view_session_ended.connect(self.canvas.hide_live_grid)
        self.controller.live_test.view_cell_updated.connect(self._on_live_cell_updated)
        
        # Temp Testing Signals
        # TODO: Wire up if needed (e.g. plotting)
        
    def _on_live_cell_updated(self, row, col, result):
        # Determine color based on result (this logic was in MainWindow/Panel)
        # For now, just set a default color or use result info
        # We need a helper to map result to color
        # Let's assume result has a 'passed' flag or similar, or we just color it blue
        from PySide6 import QtGui
        color = QtGui.QColor(0, 255, 0, 100) # Green
        self.canvas.set_live_cell_color(row, col, color)

    def closeEvent(self, event):
        self.controller.shutdown()
        super().closeEvent(event)
