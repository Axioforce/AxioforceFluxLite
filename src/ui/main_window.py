from __future__ import annotations
from typing import Dict, Optional
import os

from PySide6 import QtCore, QtWidgets, QtGui

from .. import config
from .controllers.main_controller import MainController
from .state import ViewState
from .widgets.world_canvas import WorldCanvas
from .panels.control_panel import ControlPanel
from .widgets.force_plot import ForcePlotWidget
from .widgets.moments_view import MomentsViewWidget
from .widgets.temp_plot_widget import TempPlotWidget
from .widgets.temp_slopes_widget import TempSlopesWidget
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
        
        # Start Controller (triggers autoconnect)
        self.controller.start()
        
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
        # Discrete Temp: Temp-vs-Force plot tab
        self.temp_plot_tab = TempPlotWidget()
        self.top_tabs_left.addTab(self.temp_plot_tab, "Temp Plot")

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
        # Discrete Temp: slope summary tab on the right
        self.temp_slope_tab = TempSlopesWidget()
        self.top_tabs_right.addTab(self.temp_slope_tab, "Temp Slopes")

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
        
        # Link Temp Plot -> Temp Slopes for current-plot metrics
        try:
            self.temp_plot_tab.set_slopes_widget(self.temp_slope_tab)
        except Exception:
            pass
        
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
        # User interaction: clicks on the live grid overlay
        self.canvas.live_cell_clicked.connect(self._on_live_cell_clicked)
        
        # Discrete Temp: wire test selection + plot button to Temp Plot/Slopes
        try:
            live_panel = self.controls.live_testing_panel
            live_panel.discrete_test_selected.connect(self.temp_plot_tab.set_test_path)
            live_panel.plot_test_requested.connect(self.temp_plot_tab.plot_current)
        except Exception:
            pass
        
        # Temp Testing Signals
        self._temp_analysis_payload: Optional[Dict] = None
        try:
            temp_panel = self.controls.temperature_testing_panel
            temp_ctrl = self.controller.temp_test
            # Wire analysis results
            temp_ctrl.analysis_ready.connect(self._on_temp_analysis_ready)
            temp_ctrl.grid_display_ready.connect(self._on_temp_grid_display_ready)
            # Re-render when stage changes
            temp_panel.stage_changed.connect(self._on_temp_stage_changed)
            # Plot button - goes through controller, then back to main thread for matplotlib
            temp_panel.plot_stages_requested.connect(temp_ctrl.plot_stage_detection)
            temp_ctrl.plot_ready.connect(self._on_temp_plot_ready)
        except Exception:
            pass
        
    def _on_live_cell_updated(self, row, col, result):
        # Determine color based on result
        color = QtGui.QColor(0, 255, 0, 100)  # Green default
        self.canvas.set_live_cell_color(row, col, color)

    def _on_live_cell_clicked(self, row: int, col: int) -> None:
        """Bridge canvas cell clicks into the live-test controller."""
        try:
            self.controller.live_test.handle_cell_click(int(row), int(col), {})
        except Exception:
            pass

    # --- Temperature Testing Grid Display ---

    def _on_temp_analysis_ready(self, payload: dict) -> None:
        """Handle temperature analysis results."""
        self._temp_analysis_payload = payload
        # Update metrics panel
        try:
            self.controls.temperature_testing_panel.set_analysis_metrics(payload)
        except Exception:
            pass
        # Request grid display preparation from controller
        self._request_temp_grid_update()

    def _on_temp_stage_changed(self, stage: str) -> None:
        """Re-render grids when stage filter changes."""
        if self._temp_analysis_payload:
            try:
                self.controls.temperature_testing_panel.set_analysis_metrics(self._temp_analysis_payload)
            except Exception:
                pass
            self._request_temp_grid_update()

    def _request_temp_grid_update(self) -> None:
        """Ask controller to prepare grid display data."""
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
        """Apply pre-computed cell display data to a canvas."""
        for cell in cells:
            row = int(cell.get("row", 0))
            col = int(cell.get("col", 0))
            color_bin = str(cell.get("color_bin", "green"))
            text = str(cell.get("text", ""))
            
            # Get color from bin name
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
            )
        except Exception as e:
            print(f"[MainWindow] Plot error: {e}")

    def closeEvent(self, event):
        self.controller.shutdown()
        super().closeEvent(event)
