from __future__ import annotations

from typing import Callable, Dict, Optional, Tuple, List
import os

from PySide6 import QtCore, QtWidgets, QtGui

from .. import config, data_sync
from .bridge import UiBridge
from .state import ViewState
from .widgets.world_canvas import WorldCanvas
from .panels.control_panel import ControlPanel
from .panels.live_testing_panel import LiveTestingPanel
from .panels.temperature_testing_panel import TemperatureTestingPanel
from .dialogs.live_test_setup import LiveTestSetupDialog
from .dialogs.live_test_summary import LiveTestSummaryDialog
from .dialogs.tare_prompt import TarePromptDialog
from .dialogs.model_packager import ModelPackagerDialog
from .dialogs.warmup_prompt import WarmupPromptDialog
from ..live_testing_model import GRID_BY_MODEL, LiveTestSession, LiveTestStage, GridCellResult, Thresholds
from .widgets.force_plot import ForcePlotWidget
from .widgets.moments_view import MomentsViewWidget
from .. import meta_store


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
        # Heatmap storage
        self._heatmaps = {}
        self._heatmap_metrics = {}
        self._heatmap_points_raw = {}

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
        # Discrete Temp: Temp-vs-Force plot tab (uses pyqtgraph if available)
        self.temp_plot_tab = QtWidgets.QWidget()
        tpl = QtWidgets.QVBoxLayout(self.temp_plot_tab)
        tpl.setContentsMargins(6, 6, 6, 6)
        tpl.setSpacing(6)
        self._temp_plot_pg = None
        self._temp_plot_widget = None
        try:
            import pyqtgraph as pg  # type: ignore[import-not-found]
            self._temp_plot_pg = pg
            self._temp_plot_widget = pg.PlotWidget(
                background=tuple(getattr(config, "COLOR_BG", (18, 18, 20)))
            )
            try:
                self._temp_plot_widget.showGrid(x=True, y=True, alpha=0.3)  # type: ignore[attr-defined]
                self._temp_plot_widget.setLabel("bottom", "Temperature (°F)")  # type: ignore[attr-defined]
                self._temp_plot_widget.setLabel("left", "Force")  # type: ignore[attr-defined]
            except Exception:
                pass
            tpl.addWidget(self._temp_plot_widget, 1)
        except Exception:
            # Fallback: simple label if pyqtgraph is not available
            self._temp_plot_pg = None
            self._temp_plot_widget = None
            lbl = QtWidgets.QLabel("Temperature plot requires pyqtgraph; plot output not available.")
            lbl.setStyleSheet("color: rgb(220,220,230);")
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            tpl.addWidget(lbl, 1)
        # Controls row for phase / sensor / axis selection
        ctrl_row = QtWidgets.QHBoxLayout()
        ctrl_row.setContentsMargins(0, 0, 0, 0)
        ctrl_row.setSpacing(8)
        ctrl_row.addWidget(QtWidgets.QLabel("Phase:"))
        self.temp_plot_phase_combo = QtWidgets.QComboBox()
        self.temp_plot_phase_combo.addItems(["Bodyweight", "45 lb"])
        ctrl_row.addWidget(self.temp_plot_phase_combo)
        ctrl_row.addWidget(QtWidgets.QLabel("Sensor:"))
        self.temp_plot_sensor_combo = QtWidgets.QComboBox()
        self.temp_plot_sensor_combo.addItems(
            [
                "Sum",
                "Rear Right Outer",
                "Rear Right Inner",
                "Rear Left Outer",
                "Rear Left Inner",
                "Front Left Outer",
                "Front Left Inner",
                "Front Right Outer",
                "Front Right Inner",
            ]
        )
        ctrl_row.addWidget(self.temp_plot_sensor_combo)
        ctrl_row.addWidget(QtWidgets.QLabel("Axis:"))
        self.temp_plot_axis_combo = QtWidgets.QComboBox()
        self.temp_plot_axis_combo.addItems(["z", "x", "y"])
        ctrl_row.addWidget(self.temp_plot_axis_combo)
        ctrl_row.addStretch(1)
        tpl.addLayout(ctrl_row)

        # Temp Plot summary for this tab is shown in the Temp Slopes \"Current Plot\" table on the right.
        self.top_tabs_left.addTab(self.temp_plot_tab, "Temp Plot")
        # Re-plot when any Temp Plot setting changes (if a test is selected)
        try:
            self.temp_plot_phase_combo.currentIndexChanged.connect(lambda _i: self._on_plot_discrete_test())
            self.temp_plot_sensor_combo.currentIndexChanged.connect(lambda _i: self._on_plot_discrete_test())
            self.temp_plot_axis_combo.currentIndexChanged.connect(lambda _i: self._on_plot_discrete_test())
        except Exception:
            pass
        # Live Testing UI will live in bottom control panel

        self.top_tabs_right.addTab(self.canvas_right, "Plate View")
        sensor_right = QtWidgets.QWidget()
        self._sensor_tab_right = sensor_right
        srl = QtWidgets.QVBoxLayout(sensor_right)
        srl.setContentsMargins(0, 0, 0, 0)
        self.sensor_plot_right = ForcePlotWidget()
        srl.addWidget(self.sensor_plot_right)
        self.top_tabs_right.addTab(sensor_right, "Sensor View")
        moments_right = MomentsViewWidget()
        self.moments_view_right = moments_right
        self.top_tabs_right.addTab(moments_right, "Moments View")
        # Discrete Temp: slope summary tab on the right
        self.temp_slope_tab = QtWidgets.QWidget()
        tsl = QtWidgets.QVBoxLayout(self.temp_slope_tab)
        tsl.setContentsMargins(8, 8, 8, 8)
        tsl.setSpacing(6)

        def _lbl(text: str) -> QtWidgets.QLabel:
            lab = QtWidgets.QLabel(text)
            lab.setStyleSheet("color: rgb(220,220,230);")
            return lab

        # Helper to build a 3x2 table (45/BW/All x [slope,std]) for one axis
        def _make_axis_table(title: str):
            box = QtWidgets.QGroupBox(f"{title} Axis")
            grid = QtWidgets.QGridLayout(box)
            grid.setContentsMargins(6, 6, 6, 6)
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(4)
            grid.addWidget(_lbl("Test"), 0, 0)
            grid.addWidget(_lbl("Slope"), 0, 1)
            grid.addWidget(_lbl("Std"), 0, 2)
            return box, grid

        # X axis table (raw slopes)
        x_box, x_grid = _make_axis_table("X")
        x_grid.addWidget(_lbl("45 lb"), 1, 0)
        self.lbl_slope_db_x = _lbl("—")
        self.lbl_std_db_x = _lbl("—")
        x_grid.addWidget(self.lbl_slope_db_x, 1, 1)
        x_grid.addWidget(self.lbl_std_db_x, 1, 2)
        x_grid.addWidget(_lbl("Bodyweight"), 2, 0)
        self.lbl_slope_bw_x = _lbl("—")
        self.lbl_std_bw_x = _lbl("—")
        x_grid.addWidget(self.lbl_slope_bw_x, 2, 1)
        x_grid.addWidget(self.lbl_std_bw_x, 2, 2)
        x_grid.addWidget(_lbl("All Tests"), 3, 0)
        self.lbl_slope_all_x = _lbl("—")
        self.lbl_std_all_x = _lbl("—")
        x_grid.addWidget(self.lbl_slope_all_x, 3, 1)
        x_grid.addWidget(self.lbl_std_all_x, 3, 2)

        # Y axis table
        y_box, y_grid = _make_axis_table("Y")
        y_grid.addWidget(_lbl("45 lb"), 1, 0)
        self.lbl_slope_db_y = _lbl("—")
        self.lbl_std_db_y = _lbl("—")
        y_grid.addWidget(self.lbl_slope_db_y, 1, 1)
        y_grid.addWidget(self.lbl_std_db_y, 1, 2)
        y_grid.addWidget(_lbl("Bodyweight"), 2, 0)
        self.lbl_slope_bw_y = _lbl("—")
        self.lbl_std_bw_y = _lbl("—")
        y_grid.addWidget(self.lbl_slope_bw_y, 2, 1)
        y_grid.addWidget(self.lbl_std_bw_y, 2, 2)
        y_grid.addWidget(_lbl("All Tests"), 3, 0)
        self.lbl_slope_all_y = _lbl("—")
        self.lbl_std_all_y = _lbl("—")
        y_grid.addWidget(self.lbl_slope_all_y, 3, 1)
        y_grid.addWidget(self.lbl_std_all_y, 3, 2)

        # Z axis table
        z_box, z_grid = _make_axis_table("Z")
        z_grid.addWidget(_lbl("45 lb"), 1, 0)
        self.lbl_slope_db_z = _lbl("—")
        self.lbl_std_db_z = _lbl("—")
        z_grid.addWidget(self.lbl_slope_db_z, 1, 1)
        z_grid.addWidget(self.lbl_std_db_z, 1, 2)
        z_grid.addWidget(_lbl("Bodyweight"), 2, 0)
        self.lbl_slope_bw_z = _lbl("—")
        self.lbl_std_bw_z = _lbl("—")
        z_grid.addWidget(self.lbl_slope_bw_z, 2, 1)
        z_grid.addWidget(self.lbl_std_bw_z, 2, 2)
        z_grid.addWidget(_lbl("All Tests"), 3, 0)
        self.lbl_slope_all_z = _lbl("—")
        self.lbl_std_all_z = _lbl("—")
        z_grid.addWidget(self.lbl_slope_all_z, 3, 1)
        z_grid.addWidget(self.lbl_std_all_z, 3, 2)

        # Arrange raw slope tables top-to-bottom
        tsl.addWidget(x_box)
        tsl.addWidget(y_box)
        tsl.addWidget(z_box)

        # Current-plot adjustment summary table (uses Temp Plot selection & load multiplier)
        current_box = QtWidgets.QGroupBox("Current Plot")
        cgrid = QtWidgets.QGridLayout(current_box)
        cgrid.setContentsMargins(6, 6, 6, 6)
        cgrid.setHorizontalSpacing(10)
        cgrid.setVerticalSpacing(4)
        cgrid.addWidget(_lbl("Base slope (solid line)"), 0, 0)
        self.lbl_plot_base_slope = _lbl("—")
        cgrid.addWidget(self.lbl_plot_base_slope, 0, 1)
        cgrid.addWidget(_lbl("Multiplier"), 1, 0)
        self.lbl_plot_multiplier = _lbl("—")
        cgrid.addWidget(self.lbl_plot_multiplier, 1, 1)
        cgrid.addWidget(_lbl("Adj slope (dashed line)"), 2, 0)
        self.lbl_plot_adj_slope = _lbl("—")
        cgrid.addWidget(self.lbl_plot_adj_slope, 2, 1)
        cgrid.addWidget(_lbl("% better vs base (SSE)"), 3, 0)
        self.lbl_plot_improve_pct = _lbl("—")
        cgrid.addWidget(self.lbl_plot_improve_pct, 3, 1)
        tsl.addWidget(current_box)

        tsl.addStretch(1)
        self.top_tabs_right.addTab(self.temp_slope_tab, "Temp Slopes")
        # Live Testing UI will live in bottom control panel
        self.top_tabs_right.setCurrentWidget(sensor_right)

        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.splitter.addWidget(self.top_tabs_left)
        self.splitter.addWidget(self.top_tabs_right)

        self.top_tabs_left.setMovable(True)
        self.top_tabs_right.setMovable(True)
        layout.addWidget(self.splitter)
        layout.addWidget(self.controls)
        # Reserve bottom 2/5 of available space to controls; top gets 3/5.
        try:
            layout.setStretch(0, 3)
            layout.setStretch(1, 2)
        except Exception:
            pass

        # Wire live grid cell click for retest/view
        try:
            self.canvas_left.live_cell_clicked.connect(self._on_live_cell_clicked)
            self.canvas_right.live_cell_clicked.connect(self._on_live_cell_clicked)
            # Also use clicks for calibration grid view (when no live session)
            self.canvas_left.live_cell_clicked.connect(self._on_calibration_cell_clicked)
            self.canvas_right.live_cell_clicked.connect(self._on_calibration_cell_clicked)
        except Exception:
            pass
        self.top_tabs_left.setCurrentWidget(self.canvas_left)
        try:
            self.splitter.setStretchFactor(0, 1)
            self.splitter.setStretchFactor(1, 1)
            QtCore.QTimer.singleShot(0, lambda: self.splitter.setSizes([800, 800]))
        except Exception:
            pass
        self.setCentralWidget(central)

        self.status_label = QtWidgets.QLabel("Disconnected")
        self.statusBar().addPermanentWidget(self.status_label)

        self.controls.config_changed.connect(self._on_config_changed)
        self.controls.refresh_devices_requested.connect(self._on_refresh_devices)
        self.controls.data_sync_requested.connect(self._on_data_sync_requested)
        self.controls.live_testing_tab_selected.connect(self._on_live_tab_selected)
        try:
            self.controls.live_testing_panel.load_45v_requested.connect(self._on_load_45v)
            self.controls.live_testing_panel.generate_heatmap_requested.connect(self._on_generate_heatmap)
            self.controls.live_testing_panel.heatmap_selected.connect(self._on_heatmap_selected)
            self.controls.live_testing_panel.heatmap_view_changed.connect(self._on_heatmap_view_changed)
            # Discrete temp test selection for Temps-in-Test view
            self.controls.live_testing_panel.discrete_test_selected.connect(self._on_discrete_test_selected)
            # Plot Test from Temps in Test pane
            self.controls.live_testing_panel.plot_test_requested.connect(self._on_plot_discrete_test)
        except Exception:
            pass
        self.canvas_left.mound_device_selected.connect(self._on_mound_device_selected)
        self.canvas_right.mound_device_selected.connect(self._on_mound_device_selected)
        # Keep rotations in sync across canvases
        try:
            self.canvas_left.rotation_changed.connect(lambda k: self.canvas_right.set_rotation_quadrants(k))
            self.canvas_right.rotation_changed.connect(lambda k: self.canvas_left.set_rotation_quadrants(k))
        except Exception:
            pass
        QtCore.QTimer.singleShot(500, lambda: self.controls.refresh_devices_requested.emit())

        self.bridge.snapshots_ready.connect(self.on_snapshots)
        self.bridge.connection_text_ready.connect(self.set_connection_text)
        self.bridge.single_snapshot_ready.connect(self.canvas_left.set_single_snapshot)
        self.bridge.single_snapshot_ready.connect(self.canvas_right.set_single_snapshot)
        self.bridge.single_snapshot_ready.connect(self._on_live_single_snapshot)
        self.bridge.plate_device_id_ready.connect(self.set_plate_device_id)
        self.bridge.available_devices_ready.connect(self.set_available_devices)
        self.bridge.active_devices_ready.connect(self.update_active_devices)
        self.bridge.force_vector_ready.connect(self._on_force_vector)
        self.bridge.moments_ready.connect(self._on_moments_ready)
        self.bridge.mound_force_vectors_ready.connect(self._on_mound_force_vectors)
        self.bridge.dynamo_config_ready.connect(self._on_dynamo_config)
        # Raw payload stream for discrete temp data capture
        self.bridge.raw_payload_ready.connect(self._on_live_raw_payload)
        # Model management updates
        self.bridge.model_metadata_ready.connect(self._on_model_metadata)
        self.bridge.model_package_status_ready.connect(self._on_model_package_status)
        self.bridge.model_activation_status_ready.connect(self._on_model_activation_status)

        # Live testing wiring (bottom panel)
        try:
            self.controls.live_testing_panel.start_session_requested.connect(self._on_live_start)
            # Route End Session through a handler that submits results, then cleans up
            self.controls.live_testing_panel.end_session_requested.connect(self._on_end_session_clicked)
            self.controls.live_testing_panel.next_stage_requested.connect(self._on_next_stage)
            self.controls.live_testing_panel.previous_stage_requested.connect(self._on_prev_stage)
            self.controls.live_testing_panel.package_model_requested.connect(self._on_package_model_clicked)
            self.controls.live_testing_panel.activate_model_requested.connect(self._on_activate_model)
            self.controls.live_testing_panel.deactivate_model_requested.connect(self._on_deactivate_model)
            # Discrete temperature testing wiring
            self.controls.live_testing_panel.discrete_new_requested.connect(self._on_discrete_new_test)
            self.controls.live_testing_panel.discrete_add_requested.connect(self._on_discrete_add_existing)
            # Temperature Testing wiring
            self.controls.temperature_testing_panel.device_selected.connect(self._on_temp_device_selected)
            self.controls.temperature_testing_panel.refresh_requested.connect(self._on_temp_refresh_devices)
            self.controls.temperature_testing_panel.run_requested.connect(self._on_temp_run_requested)
            self.controls.temperature_testing_panel.test_changed.connect(self._on_temp_test_changed)
            self.controls.temperature_testing_panel.processed_selected.connect(self._on_temp_processed_selected)
            self.controls.temperature_testing_panel.view_mode_changed.connect(self._on_temp_view_mode_changed)
            self.controls.temperature_testing_panel.stage_changed.connect(self._on_temp_stage_changed)
            self.controls.temperature_testing_panel.target_changed.connect(lambda _: self._apply_temp_views())
        except Exception:
            pass
        
        # Initial device scan for temp testing
        QtCore.QTimer.singleShot(1000, lambda: self._on_temp_refresh_devices())

        self._live_session: Optional[LiveTestSession] = None
        self._live_stage_idx: int = 0
        self._active_cell: Optional[Tuple[int, int]] = None
        self._stability_window_ms: int = 2000
        self._stability_tolerance_ms: int = 40  # allow slight under-run due to sample discretization
        self._recent_samples: list[Tuple[int, float, float, float]] = []  # (t_ms, x_mm, y_mm, |fz_n|)
        # Track the active ML model label from the Model pane for display/export
        self._active_model_label: Optional[str] = None
        # Arming: require >= 50 N sustained for 2.0 s within the same cell (continuous)
        self._arming_window_ms: int = 2000
        self._arming_cell: Optional[Tuple[int, int]] = None
        self._arming_start_ms: Optional[int] = None
        # Stage/time tracking for processed slicing
        self._last_snapshot_time_ms: Optional[int] = None
        self._stage_mark_active_idx: Optional[int] = None
        self._stage_mark_pending_start: bool = False
        # Temperature Test: track cells completed in current stage "turn" for auto-switching
        self._temp_cells_this_turn: int = 0
        # Temperature Test: pending stage index to switch to once Fz drops below threshold
        self._temp_pending_stage_idx: Optional[int] = None

        # Automated tare scheduler state
        self._next_tare_due_ms: Optional[int] = None
        self._tare_dialog: Optional[TarePromptDialog] = None
        self._tare_active: bool = False
        self._tare_countdown_remaining_s: int = 0
        self._tare_last_tick_s: Optional[int] = None
        # Discrete temp testing: track dedicated tare/flow state
        self._discrete_tare_mode: bool = False
        self._discrete_ready_for_data: bool = False
        self._discrete_waiting_for_unload: bool = False
        # When True in a discrete temp session, the next tare (auto or manual)
        # should be treated as completing the discrete tare sequence and
        # advancing the flow.
        self._discrete_pending_tare_complete: bool = False
        # Discrete Temp: currently selected test path for Temps-in-Test / Temp Plot
        self._selected_discrete_test_path: str = ""
        # Discrete Temp: cached per-sensor slopes and aggregated slope summaries
        self._temp_slopes_by_sensor: Dict[str, Dict[str, Dict[str, float]]] = {}  # phase -> axis -> sensor_prefix -> slope
        self._temp_slope_avgs: Dict[str, Dict[str, float]] = {}  # "bodyweight"/"45lb"/"all" -> axis -> slope
        self._temp_slope_stds: Dict[str, Dict[str, float]] = {}  # "bodyweight"/"45lb"/"all" -> axis -> std
        # Discrete Temp: load-adjusted (weight-scaled) slope model per axis
        # axis -> {"base": float, "k45": float, "kBW": float, "F45": float, "FBW": float, "a": float, "b": float}
        self._temp_weight_models: Dict[str, Dict[str, float]] = {}
        # Discrete Temp: last plotted adjustment stats (for Temp Slopes "Current Plot" table)
        self._temp_plot_last_base_slope: Optional[float] = None
        self._temp_plot_last_multiplier: Optional[float] = None
        self._temp_plot_last_adj_slope: Optional[float] = None
        self._temp_plot_last_improve_pct: Optional[float] = None
        self._temp_plot_last_a: Optional[float] = None
        self._temp_plot_last_b: Optional[float] = None
        self._temp_plot_last_Fref: Optional[float] = None
        self._temp_plot_last_is_sum: bool = False
        # Sensor View temperature smoothing (15 s rolling average for selected device)
        self._sensor_temp_buffer: list[Tuple[int, float]] = []  # (t_ms, tempF)
        # Temperature Test: track warmup state so tare dialog does not appear early
        self._temp_warmup_active: bool = False
        self._sensor_temp_smoothed_f: Optional[float] = None

        # Cell-capture sound effect (plays after each successful cell reading in live testing)
        self._capture_sound_player = None
        try:
            from PySide6 import QtMultimedia as _QtMM  # type: ignore[import]
        except Exception:
            _QtMM = None
        if _QtMM is not None:
            try:
                sound_path = os.path.join(self._repo_root(), "assets", "sound", "cell_capture_success.mp3")
            except Exception:
                sound_path = ""
            if sound_path and os.path.isfile(sound_path):
                try:
                    audio_out = _QtMM.QAudioOutput(self)
                    audio_out.setVolume(0.8)
                    player = _QtMM.QMediaPlayer(self)
                    player.setAudioOutput(audio_out)
                    player.setSource(QtCore.QUrl.fromLocalFile(sound_path))
                    self._capture_sound_player = player
                    self._capture_sound_output = audio_out
                except Exception:
                    self._capture_sound_player = None

        # Backend config/capture wiring (callbacks set by controller via main.py)
        self._on_update_dynamo_config_cb: Optional[Callable[[str, object], None]] = None
        self._on_set_model_bypass_cb: Optional[Callable[[bool], None]] = None
        self._resolve_group_id_cb: Optional[Callable[[str], Optional[str]]] = None
        self._on_apply_temp_corr_cb: Optional[Callable[[], None]] = None
        # Temp testing: pending device for metadata update
        self._temp_meta_device_id: Optional[str] = None
        # Track capture lifecycle tied to live session
        self._capture_active: bool = False
        self._capture_group_id: str = ""
        # Track if we enabled model bypass to revert at end
        self._should_revert_bypass: bool = False

        # Initialize live testing start button availability
        try:
            self._update_live_start_enabled()
        except Exception:
            pass

        # Reflect backend rate changes to controls
        try:
            self.controls.sampling_rate_changed.connect(self._on_sampling_rate_changed)
            self.controls.emission_rate_changed.connect(self._on_emission_rate_changed)
            # Interface controls wiring
            self.controls.ui_tick_hz_changed.connect(self._on_ui_tick_hz_changed)
            self.controls.autoscale_damp_toggled.connect(self._on_autoscale_damp_toggled)
            self.controls.autoscale_damp_n_changed.connect(self._on_autoscale_damp_n_changed)
            # Backend Config quick actions (Config tab)
            self.controls.backend_model_bypass_changed.connect(self._on_backend_model_bypass_changed)
            self.controls.backend_capture_detail_changed.connect(self._on_backend_capture_detail_changed)
            self.controls.backend_temperature_apply_requested.connect(self._on_backend_temperature_apply)
        except Exception:
            pass

        # Controller callbacks for model events
        self._request_model_metadata_cb: Optional[Callable[[str], None]] = None
        self._on_package_model_cb: Optional[Callable[[dict], None]] = None
        self._on_activate_model_cb: Optional[Callable[[str, str], None]] = None
        self._on_deactivate_model_cb: Optional[Callable[[str, str], None]] = None

    # --- Helpers ---------------------------------------------------------------
    def _is_model_entry_active(self, entry: object) -> bool:
        try:
            e = entry or {}
            # Accept multiple key styles
            raw = (
                e.get("active")
                or e.get("model_active")
                or e.get("modelActive")
                or e.get("isActive")
            )
            # Normalize booleans/ints/strings
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, (int, float)):
                return bool(int(raw))
            if isinstance(raw, str):
                s = raw.strip().lower()
                if s in ("true", "yes", "on", "1"): return True
                if s in ("false", "no", "off", "0", "none", "null", ""): return False
                # Non-empty unexpected string – be conservative
                return False
            return False
        except Exception:
            return False

    def _log(self, msg: str) -> None:
        try:
            print(f"[live] {msg}", flush=True)
        except Exception:
            pass

    def on_snapshots(self, snaps: Dict[str, Tuple[float, float, float, int, bool, float, float]], hz_text: Optional[str]) -> None:
        self.canvas_left.set_snapshots(snaps)
        self.canvas_right.set_snapshots(snaps)

    def set_connection_text(self, txt: str) -> None:
        self.status_label.setText(txt)

    def _on_ui_tick_hz_changed(self, hz: int) -> None:
        if hasattr(self, '_on_ui_tick_cb') and callable(self._on_ui_tick_cb):
            try:
                self._on_ui_tick_cb(int(hz))
            except Exception:
                pass

    def _on_autoscale_damp_toggled(self, enabled: bool) -> None:
        try:
            every = int(self.controls.autoscale_every_spin.value())
        except Exception:
            every = 2
        # Apply immediately and also persist to config so new widgets align
        try:
            setattr(config, 'PLOT_AUTOSCALE_DAMP_ENABLED', bool(enabled))
            setattr(config, 'PLOT_AUTOSCALE_DAMP_EVERY_N', int(every))
        except Exception:
            pass
        self._apply_plot_autoscale_settings(bool(enabled), int(every))

    def _on_autoscale_damp_n_changed(self, every_n: int) -> None:
        try:
            enabled = bool(self.controls.chk_autoscale_damp.isChecked())
        except Exception:
            enabled = True
        try:
            setattr(config, 'PLOT_AUTOSCALE_DAMP_EVERY_N', int(every_n))
        except Exception:
            pass
        self._apply_plot_autoscale_settings(bool(enabled), int(every_n))

    def _on_dynamo_config(self, cfg: dict) -> None:
        try:
            sampling = int(cfg.get('samplingRate') or 0)
        except Exception:
            sampling = 0
        try:
            emission = int(cfg.get('emissionRate') or 0)
        except Exception:
            emission = 0
        try:
            self.controls.set_backend_rates(sampling, emission)
        except Exception:
            pass

    def _on_sampling_rate_changed(self, hz: int) -> None:
        if hasattr(self, '_on_sampling_cb') and callable(self._on_sampling_cb):
            try:
                self._on_sampling_cb(int(hz))
            except Exception:
                pass

    def _on_emission_rate_changed(self, hz: int) -> None:
        if hasattr(self, '_on_emission_cb') and callable(self._on_emission_cb):
            try:
                self._on_emission_cb(int(hz))
            except Exception:
                pass

    # Backend Config quick actions ------------------------------------------------
    def _on_backend_model_bypass_changed(self, enabled: bool) -> None:
        """Toggle model bypass via controller callback."""
        try:
            if callable(getattr(self, "_on_set_model_bypass_cb", None)):
                self._on_set_model_bypass_cb(bool(enabled))
        except Exception:
            pass

    def _on_backend_capture_detail_changed(self, value: str) -> None:
        """Update captureDetail via updateDynamoConfig."""
        try:
            v = (value or "").strip()
            if not v:
                return
            if callable(getattr(self, "_on_update_dynamo_config_cb", None)):
                self._on_update_dynamo_config_cb("captureDetail", v)
        except Exception:
            pass

    def _on_backend_temperature_apply(self, payload: object) -> None:
        """Apply temperature correction settings (use flag, slopes, optional ambient)."""
        try:
            if not isinstance(payload, dict):
                return
            use_tc = bool(payload.get("use_temperature_correction", False))
            slopes = payload.get("slopes") or {}
            room_temp_f = payload.get("room_temperature_f", None)
            # Normalize slopes
            try:
                sx = float(slopes.get("x", 0.0))
                sy = float(slopes.get("y", 0.0))
                sz = float(slopes.get("z", 0.0))
            except Exception:
                sx = sy = sz = 0.0
            if callable(getattr(self, "_on_update_dynamo_config_cb", None)):
                # Backend expects camelCase keys; server converts to snake internally
                self._on_update_dynamo_config_cb("useTemperatureCorrection", use_tc)
                self._on_update_dynamo_config_cb(
                    "temperatureCorrection", {"x": sx, "y": sy, "z": sz}
                )
                if room_temp_f is not None:
                    try:
                        self._on_update_dynamo_config_cb("roomTemperatureF", float(room_temp_f))
                    except Exception:
                        pass
            # Apply to running devices
            if callable(getattr(self, "_on_apply_temp_corr_cb", None)):
                self._on_apply_temp_corr_cb()
        except Exception:
            pass

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
        """Connect backend tare handler, with discrete-temp flow integration.

        In discrete temp sessions, a manual tare (e.g., after cancelling the auto-tare
        dialog) should still count as completing the discrete tare sequence and advance
        the flow just like an auto-tare.
        """

        def _wrapped_tare(gid: str) -> None:
            # Always forward to backend/controller first
            slot(gid)
            # In discrete temp mode, treat manual tares as completing any pending
            # discrete tare sequence (e.g., after cancelling the guidance dialog).
            try:
                if (
                    self._live_session is not None
                    and bool(getattr(self._live_session, "is_discrete_temp", False))
                    and bool(getattr(self, "_discrete_pending_tare_complete", False))
                ):
                    self._on_discrete_tare_completed()
            except Exception:
                pass

        self.controls.tare_requested.connect(_wrapped_tare)

    def on_config_changed(self, slot: Callable[[], None]) -> None:
        self.controls.config_changed.connect(slot)

    def on_sampling_rate_changed(self, slot: Callable[[int], None]) -> None:
        self._on_sampling_cb = slot

    def on_emission_rate_changed(self, slot: Callable[[int], None]) -> None:
        self._on_emission_cb = slot

    # Interface callbacks registration (controller can subscribe)
    def on_ui_tick_hz_changed(self, slot: Callable[[int], None]) -> None:
        self._on_ui_tick_cb = slot

    # Apply plot autoscale settings to both plots
    def _apply_plot_autoscale_settings(self, enabled: bool, every_n: int) -> None:
        try:
            if hasattr(self, 'sensor_plot_left') and self.sensor_plot_left is not None:
                self.sensor_plot_left.set_autoscale_damping(bool(enabled), int(every_n))
            if hasattr(self, 'sensor_plot_right') and self.sensor_plot_right is not None:
                self.sensor_plot_right.set_autoscale_damping(bool(enabled), int(every_n))
        except Exception:
            pass

    def on_request_model_metadata(self, slot: Callable[[str], None]) -> None:
        self._request_model_metadata_cb = slot

    def on_package_model(self, slot: Callable[[dict], None]) -> None:
        self._on_package_model_cb = slot

    def on_activate_model(self, slot: Callable[[str, str], None]) -> None:
        self._on_activate_model_cb = slot

    def on_deactivate_model(self, slot: Callable[[str, str], None]) -> None:
        self._on_deactivate_model_cb = slot

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
            # Reset Sensor View temperature smoothing when config/device changes
            self._sensor_temp_buffer.clear()
            self._sensor_temp_smoothed_f = None
            try:
                self.sensor_plot_right.set_temperature_f(None)
            except Exception:
                pass
            # Show dual-series legend only in mound mode
            is_mound = getattr(self, "state", None) is not None and getattr(self.state, "display_mode", "") == "mound"
            self.sensor_plot_left.set_dual_series_enabled(bool(is_mound))
            self.sensor_plot_right.set_dual_series_enabled(bool(is_mound))
            # Update live testing start enabled state on any config change
            self._update_live_start_enabled()
            # Update calibration button state as well
            self._update_calibration_enabled()
            self._log(f"config_changed: display_mode={self.state.display_mode}, selected_device_id={self.state.selected_device_id}, selected_device_type={self.state.selected_device_type}")
        except Exception:
            pass

    def _on_data_sync_requested(self, onedrive_root: str) -> None:
        """Handle OneDrive data sync requests from the Config tab."""
        try:
            onedrive_root = str(onedrive_root or "").strip()
        except Exception:
            onedrive_root = ""
        if not onedrive_root:
            try:
                QtWidgets.QMessageBox.warning(self, "Data Sync", "Please select a OneDrive data root before syncing.")
            except Exception:
                pass
            return
        # Persist the path for this machine and normalize discrete-temp folder layout before sync
        try:
            data_sync.set_onedrive_data_root(onedrive_root)
        except Exception:
            pass
        # Best-effort: ensure discrete_temp_testing uses date/tester folder structure locally
        try:
            self._normalize_discrete_temp_structure()
        except Exception:
            pass
        try:
            data_sync.sync_all_data(onedrive_root)
            try:
                QtWidgets.QMessageBox.information(self, "Data Sync", "CSV data synchronized with OneDrive.")
            except Exception:
                pass
        except Exception:
            try:
                QtWidgets.QMessageBox.warning(self, "Data Sync", "An error occurred while syncing data. See console for details.")
            except Exception:
                pass

    def _update_live_start_enabled(self) -> None:
        single_mode = (self.state.display_mode == "single")
        has_device = bool((self.state.selected_device_id or "").strip())
        enabled = bool(single_mode and has_device and self._live_session is None)
        if hasattr(self.controls, "live_testing_panel"):
            try:
                self.controls.live_testing_panel.btn_start.setEnabled(enabled)
                # Refresh discrete temp testing picker for current device
                self._refresh_discrete_tests_for_current_device()
                self._log(f"update_live_start_enabled: single_mode={single_mode}, has_device={has_device}, enabled={enabled}")
            except Exception:
                pass

    def _update_calibration_enabled(self) -> None:
        try:
            has_device = bool((self.state.selected_device_id or "").strip())
            active_model = (getattr(self, "_active_model_label", None) or "").strip()
            has_active_model = bool(active_model and active_model not in ("—", "No Model", "Loading…"))
            enabled = bool(has_device and has_active_model)
            if hasattr(self.controls, "live_testing_panel"):
                self.controls.live_testing_panel.set_calibration_enabled(enabled)
        except Exception:
            pass

    # --- Discrete Temperature Testing helpers ---------------------------------

    def _repo_root(self) -> str:
        """Return project root (two levels up from this file)."""
        import os
        try:
            return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        except Exception:
            return os.getcwd()

    def _normalize_device_folder(self, device_id: str) -> str:
        """Normalize device id for use as a folder name."""
        s = (device_id or "").strip()
        if not s:
            return ""
        # Keep alphanumerics and common separators for readability
        return "".join(ch for ch in s if ch.isalnum() or ch in (".", "-", "_"))

    def _short_device_label(self, device_id: str) -> str:
        """Derive a short label from full device id as: first two chars + '-' + last two chars."""
        s = (device_id or "").strip()
        if len(s) >= 4:
            return f"{s[:2]}-{s[-2:]}"
        return s or "Device"

    def _discrete_tests_root_for_device(self, device_id: str) -> str:
        """Return path to discrete_temp_testing/<device> for given device id."""
        import os
        root = os.path.join(self._repo_root(), "discrete_temp_testing")
        dev_norm = self._normalize_device_folder(device_id)
        return os.path.join(root, dev_norm or "unknown")

    def _normalize_tester_folder(self, tester_name: str) -> str:
        """Normalize tester name for use as a folder: lowercase, spaces->'_', alnum/-/_ only."""
        s = (tester_name or "").strip().lower()
        s = s.replace(" ", "_")
        if not s:
            s = "unknown"
        return "".join(ch for ch in s if ch.isalnum() or ch in ("-", "_")) or "unknown"

    def _normalize_discrete_temp_structure(self) -> None:
        """Ensure discrete_temp_testing uses <device>/<date>/<tester>/... layout based on test_meta.json.
        
        Handles mixed content (files in date root alongside tester folders) by safely moving/merging files.
        """
        import os, json, shutil, datetime, csv
        root = os.path.join(self._repo_root(), "discrete_temp_testing")
        if not os.path.isdir(root):
            return
        for dev_folder in os.listdir(root):
            plate_dir = os.path.join(root, dev_folder)
            if not os.path.isdir(plate_dir):
                continue
            for date_name in os.listdir(plate_dir):
                date_dir = os.path.join(plate_dir, date_name)
                if not os.path.isdir(date_dir):
                    continue
                
                # Check for test_meta.json in the date root to identify the tester
                meta_path = os.path.join(date_dir, "test_meta.json")
                if not os.path.isfile(meta_path):
                    continue
                
                tester = ""
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f) or {}
                    tester = str(meta.get("tester_name") or meta.get("tester") or "").strip()
                except Exception:
                    tester = ""
                
                tester_norm = self._normalize_tester_folder(tester)
                target_dir = os.path.join(date_dir, tester_norm)
                
                # If target is same as source, skip
                if os.path.abspath(target_dir) == os.path.abspath(date_dir):
                    continue
                
                os.makedirs(target_dir, exist_ok=True)
                
                # Move all files from date_dir to target_dir
                for name in os.listdir(date_dir):
                    src = os.path.join(date_dir, name)
                    
                    # Skip directories (including the target_dir itself)
                    if os.path.isdir(src):
                        continue
                        
                    dst = os.path.join(target_dir, name)
                    
                    # Safe move/merge
                    if not os.path.exists(dst):
                        try:
                            shutil.move(src, dst)
                        except Exception:
                            pass
                    else:
                        # File exists. If it's a CSV, try to append rows.
                        if name.lower().endswith(".csv"):
                            try:
                                src_rows = []
                                with open(src, "r", encoding="utf-8", newline="") as f_src:
                                    reader = csv.reader(f_src)
                                    src_rows = list(reader)
                                
                                if src_rows:
                                    with open(dst, "a", encoding="utf-8", newline="") as f_dst:
                                        writer = csv.writer(f_dst)
                                        for i, row in enumerate(src_rows):
                                            # Skip header row if present (simple heuristic: contains 'time' and 'phase')
                                            if i == 0 and len(row) > 1 and "time" in str(row[0]).lower():
                                                continue
                                            writer.writerow(row)
                                
                                # Delete source after successful append
                                os.remove(src)
                            except Exception:
                                pass
                        else:
                            # For non-CSV (like json), if it exists in target, we assume target is authoritative.
                            # We remove the source duplicate to clean up.
                            try:
                                os.remove(src)
                            except Exception:
                                pass

    def _refresh_discrete_tests_for_current_device(self) -> None:
        """Scan filesystem and populate discrete tests picker with all discrete-temp tests."""
        if not hasattr(self.controls, "live_testing_panel"):
            return
        import os, json, datetime
        tests: list[tuple[str, str, str]] = []
        try:
            root = os.path.join(self._repo_root(), "discrete_temp_testing")
            if os.path.isdir(root):
                # Iterate over all device folders underneath root
                for dev_folder in sorted(os.listdir(root)):
                    plate_dir = os.path.join(root, dev_folder)
                    if not os.path.isdir(plate_dir):
                        continue
                    dev_id = str(dev_folder)
                    short_label = self._short_device_label(dev_id)
                    for date_name in sorted(os.listdir(plate_dir)):
                        day_path = os.path.join(plate_dir, date_name)
                        if not os.path.isdir(day_path):
                            continue
                        # First, handle new structure: per-tester subfolders under date
                        has_subdirs = False
                        try:
                            for tester_folder in sorted(os.listdir(day_path)):
                                tester_dir = os.path.join(day_path, tester_folder)
                                if not os.path.isdir(tester_dir):
                                    continue
                                has_subdirs = True
                                meta_path = os.path.join(tester_dir, "test_meta.json")
                                meta = {}
                                if os.path.isfile(meta_path):
                                    try:
                                        with open(meta_path, "r", encoding="utf-8") as f:
                                            meta = json.load(f) or {}
                                    except Exception:
                                        meta = {}
                                tester = str(meta.get("tester_name") or meta.get("tester") or "").strip() or str(tester_folder)
                                left = short_label
                                if tester:
                                    left = f"{short_label}_{tester}"
                                # Date for display: folder name MM-DD-YYYY -> MM/DD/YYYY
                                date_str = date_name
                                try:
                                    dt = datetime.datetime.strptime(date_name, "%m-%d-%Y")
                                    date_str = dt.strftime("%m/%d/%Y")
                                except Exception:
                                    date_str = date_name.replace("-", "/").replace("_", "/")
                                label = f"{left}"
                                tests.append((label, date_str, tester_dir))
                        except Exception:
                            has_subdirs = False
                        # Back-compat: legacy layout with files directly under date folder
                        if not has_subdirs:
                            meta_path = os.path.join(day_path, "test_meta.json")
                            meta = {}
                            if os.path.isfile(meta_path):
                                try:
                                    with open(meta_path, "r", encoding="utf-8") as f:
                                        meta = json.load(f) or {}
                                except Exception:
                                    meta = {}
                            tester = str(meta.get("tester_name") or meta.get("tester") or "").strip()
                            left = short_label
                            if tester:
                                left = f"{short_label}_{tester}"
                            date_str = date_name
                            try:
                                dt = datetime.datetime.strptime(date_name, "%m-%d-%Y")
                                date_str = dt.strftime("%m/%d/%Y")
                            except Exception:
                                date_str = date_name.replace("-", "/").replace("_", "/")
                            label = f"{left}"
                            tests.append((label, date_str, day_path))
        except Exception:
            tests = []
        try:
            self.controls.live_testing_panel.set_discrete_tests(tests)
        except Exception:
            pass

    def _on_discrete_new_test(self) -> None:
        """Create a new discrete temp test folder for the current plate and today, then start a session."""
        # Only one live session at a time
        if self._live_session is not None:
            try:
                QtWidgets.QMessageBox.information(self, "Discrete Temp Testing", "End the current session before starting a new discrete temp test.")
            except Exception:
                pass
            return
        dev_id = (self.state.selected_device_id or "").strip()
        if not dev_id or self.state.display_mode != "single":
            try:
                QtWidgets.QMessageBox.warning(self, "Discrete Temp Testing", "Select a single plate in Config before starting a discrete temp test.")
            except Exception:
                pass
            return
        import os, json, time, datetime
        plate_dir = self._discrete_tests_root_for_device(dev_id)
        try:
            os.makedirs(plate_dir, exist_ok=True)
        except Exception:
            pass
        today_str = datetime.datetime.now().strftime("%m-%d-%Y")
        # For new tests, start with a date folder; once tester is known, we may move into a per-tester subfolder.
        date_dir = os.path.join(plate_dir, today_str)
        try:
            os.makedirs(date_dir, exist_ok=True)
        except Exception:
            pass
        meta_path = os.path.join(date_dir, "test_meta.json")
        if not os.path.isfile(meta_path):
            meta = {
                "device_id": dev_id,
                "short_label": self._short_device_label(dev_id),
                "tester_name": "",
                "date": today_str,
                "created_at_ms": int(time.time() * 1000),
            }
            try:
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(meta, f, indent=2)
            except Exception:
                pass
        # Remember active test path and refresh picker
        try:
            self._active_discrete_test_path = str(date_dir)
        except Exception:
            self._active_discrete_test_path = date_dir
        self._refresh_discrete_tests_for_current_device()
        # When starting a new discrete session, focus on Plate/Sensor views
        try:
            self.top_tabs_left.setCurrentWidget(self.canvas_left)
        except Exception:
            pass
        try:
            self.top_tabs_right.setCurrentWidget(self._sensor_tab_right)
        except Exception:
            pass
        # Start discrete temp live session for this test (date folder; may be moved into tester subfolder)
        self._start_discrete_temp_session(date_dir)

    def _on_discrete_test_selected(self, test_path: str) -> None:
        """Update Temps-in-Test tab when a discrete test is selected."""
        # No selection: clear the UI and return
        if not test_path:
            try:
                self._selected_discrete_test_path = ""
                self.controls.live_testing_panel.set_temps_in_test(None, [])
                # Clear slope summaries as well
                self._temp_slopes_by_sensor = {}
                self._temp_slope_avgs = {}
                self._update_temp_slope_panel()
            except Exception:
                pass
            return

        try:
            self._selected_discrete_test_path = str(test_path or "")
        except Exception:
            self._selected_discrete_test_path = ""

        includes_baseline = False
        temps_f: list[float] = []
        try:
            import os, csv
            csv_path = os.path.join(test_path, "discrete_temp_session.csv")
            if not os.path.isfile(csv_path) or os.path.getsize(csv_path) <= 0:
                raise FileNotFoundError
            with open(csv_path, "r", encoding="utf-8", newline="") as f:
                # Use skipinitialspace to handle leading spaces
                reader = csv.DictReader(f, skipinitialspace=True)
                sessions: dict[str, list[float]] = {}
                for row in reader:
                    if not row:
                        continue
                    # Robust key normalization: strip all keys in the row
                    clean_row = {k.strip(): v for k, v in row.items() if k}
                    
                    key = str(clean_row.get("time") or "")
                    if not key:
                        continue
                    try:
                        temp_val = float(clean_row.get("sum-t") or 0.0)
                    except Exception:
                        continue
                    sessions.setdefault(key, []).append(temp_val)
            if not sessions:
                raise ValueError("no sessions")
            session_temps: list[float] = []
            for vals in sessions.values():
                if not vals:
                    continue
                avg = sum(vals) / float(len(vals))
                session_temps.append(avg)
            if not session_temps:
                raise ValueError("no temps")
            baseline_low = 74.0
            baseline_high = 78.0
            non_baseline: list[float] = []
            for t in session_temps:
                if baseline_low <= t <= baseline_high:
                    includes_baseline = True
                else:
                    non_baseline.append(t)
            temps_f = sorted(non_baseline, reverse=True)
        except Exception:
            includes_baseline = False
            temps_f = []
        try:
            self.controls.live_testing_panel.set_temps_in_test(includes_baseline, temps_f)
        except Exception:
            pass
        # Recompute slope summaries for this test and update the right-hand pane
        try:
            self._compute_discrete_temp_slopes(csv_path)
            self._update_temp_slope_panel()
        except Exception:
            # Best-effort; leave previous slopes if computation fails
            pass
        # If Temp Plot is available, refresh it for the newly selected test and show Temp Slopes
        try:
            self._on_plot_discrete_test()
        except Exception:
            pass
        try:
            self.top_tabs_right.setCurrentWidget(self.temp_slope_tab)
        except Exception:
            pass

    def _compute_discrete_temp_slopes(self, csv_path: str) -> None:
        """Compute per-sensor temperature slopes for x/y/z and aggregate them."""
        import csv, math
        # Initialize containers: phase -> axis -> sensor_prefix -> list[(T, value)]
        phases = ("45lb", "bodyweight")
        axes = ("x", "y", "z")
        # Only individual sensors; exclude Sum
        sensor_prefixes = [
            "rear-right-outer",
            "rear-right-inner",
            "rear-left-outer",
            "rear-left-inner",
            "front-left-outer",
            "front-left-inner",
            "front-right-outer",
            "front-right-inner",
        ]
        data: Dict[str, Dict[str, Dict[str, List[Tuple[float, float]]]]] = {
            ph: {ax: {sp: [] for sp in sensor_prefixes} for ax in axes} for ph in phases
        }
        # Track average per-sensor load for each phase using |sum-z|/8.0
        phase_loads: Dict[str, List[float]] = {ph: [] for ph in phases}
        try:
            with open(csv_path, "r", encoding="utf-8", newline="") as f:
                # Read header line manually to strip whitespace
                header_line = f.readline()
                if not header_line:
                    return
                import io
                header_reader = csv.reader(io.StringIO(header_line))
                headers = next(header_reader, [])
                headers = [h.strip() for h in headers]
                # Use skipinitialspace to handle leading spaces in data, though headers are now manual
                reader = csv.DictReader(f, fieldnames=headers, skipinitialspace=True)

                for row in reader:
                    if not row:
                        continue
                    # Robust key normalization: strip all keys in the row
                    clean_row = {k.strip(): v for k, v in row.items() if k}
                    
                    try:
                        phase_raw = str(clean_row.get("phase_name") or clean_row.get("phase") or "").strip().lower()
                    except Exception:
                        continue
                    if phase_raw not in ("45lb", "bodyweight"):
                        continue
                    phase = phase_raw
                    try:
                        temp_f = float(clean_row.get("sum-t") or 0.0)
                    except Exception:
                        continue
                    # Average per-sensor load from sum-z
                    try:
                        sum_z = float(clean_row.get("sum-z") or 0.0)
                        if sum_z != 0.0:
                            phase_loads[phase].append(abs(sum_z) / 8.0)
                    except Exception:
                        pass
                    for sp in sensor_prefixes:
                        for ax in axes:
                            col = f"{sp}-{ax}"
                            try:
                                val = float(clean_row.get(col) or 0.0)
                            except Exception:
                                continue
                            data[phase][ax][sp].append((temp_f, val))
        except Exception:
            # On any I/O failure, clear slopes
            self._temp_slopes_by_sensor = {}
            self._temp_slope_avgs = {}
            self._temp_weight_models = {}
            return
        slopes_by_sensor: Dict[str, Dict[str, Dict[str, float]]] = {ph: {ax: {} for ax in axes} for ph in phases}
        slope_lists_phase: Dict[str, Dict[str, List[float]]] = {ph: {ax: [] for ax in axes} for ph in phases}
        slope_lists_all: Dict[str, List[float]] = {ax: [] for ax in axes}

        def _fit_slope(points: List[Tuple[float, float]]) -> float:
            """Fit slope with baseline constraint when baseline exists; otherwise ordinary least squares.

            Baseline handling:
            - Use all points with 74°F <= T <= 78°F as potential baselines.
            - Compute a weighted baseline anchor (T0, Y0) where weights bias toward 76°F:
                  w_i = 1 / (1 + |T_i - 76|)
              so that temperatures closest to 76°F dominate when multiple baseline
              measurements exist for a given sensor.
            """
            if len(points) < 2:
                return 0.0
            # Baseline range around 76°F
            baseline_low = 74.0
            baseline_high = 78.0
            target_T = 76.0
            baseline = [(t, y) for (t, y) in points if baseline_low <= t <= baseline_high]
            if baseline:
                # Weighted baseline anchor biased toward 76°F
                try:
                    weights: List[float] = []
                    for t, _y in baseline:
                        try:
                            dt = abs(float(t) - target_T)
                        except Exception:
                            dt = 0.0
                        w = 1.0 / (1.0 + dt)
                        weights.append(w)
                    w_sum = sum(weights)
                    if w_sum <= 0.0:
                        raise ValueError("baseline_weight_sum_zero")
                    T0 = sum(w * t for (w, (t, _)) in zip(weights, baseline)) / w_sum
                    Y0 = sum(w * y for (w, (_, y)) in zip(weights, baseline)) / w_sum
                except Exception:
                    # Fallback: simple mean if weighting fails for any reason
                    try:
                        T0 = sum(t for t, _ in baseline) / float(len(baseline))
                        Y0 = sum(y for _, y in baseline) / float(len(baseline))
                    except Exception:
                        T0, Y0 = baseline[0]
                num = 0.0
                den = 0.0
                for (t, y) in points:
                    dt = t - T0
                    dy = y - Y0
                    num += dt * dy
                    den += dt * dt
                if den <= 0.0:
                    return 0.0
                return num / den
            # Ordinary least squares
            try:
                mean_t = sum(t for t, _ in points) / float(len(points))
                mean_y = sum(y for _, y in points) / float(len(points))
            except Exception:
                return 0.0
            num = 0.0
            den = 0.0
            for (t, y) in points:
                dt = t - mean_t
                dy = y - mean_y
                num += dt * dy
                den += dt * dt
            if den <= 0.0:
                return 0.0
            return num / den

        for ph in phases:
            for ax in axes:
                for sp, pts in data.get(ph, {}).get(ax, {}).items():
                    if not pts or len(pts) < 2:
                        continue
                    try:
                        m = _fit_slope(pts)
                    except Exception:
                        m = 0.0
                    slopes_by_sensor[ph][ax][sp] = m
                    slope_lists_phase[ph][ax].append(m)
                    slope_lists_all[ax].append(m)

        avgs: Dict[str, Dict[str, float]] = {"bodyweight": {}, "45lb": {}, "all": {}}
        stds: Dict[str, Dict[str, float]] = {"bodyweight": {}, "45lb": {}, "all": {}}
        weight_models: Dict[str, Dict[str, float]] = {}
        for ax in axes:
            for ph in phases:
                vals = slope_lists_phase[ph][ax]
                if vals:
                    mu = sum(vals) / float(len(vals))
                    var = sum((v - mu) ** 2 for v in vals) / float(len(vals))
                    avgs[ph][ax] = mu
                    stds[ph][ax] = var ** 0.5
                else:
                    avgs[ph][ax] = 0.0
                    stds[ph][ax] = 0.0
            vals_all = slope_lists_all[ax]
            if vals_all:
                mu_all = sum(vals_all) / float(len(vals_all))
                var_all = sum((v - mu_all) ** 2 for v in vals_all) / float(len(vals_all))
                avgs["all"][ax] = mu_all
                stds["all"][ax] = var_all ** 0.5
            else:
                avgs["all"][ax] = 0.0
                stds["all"][ax] = 0.0

            # Build simple linear "multiplier vs load" model for this axis using phase-averaged loads.
            # We treat the all-tests slope as a base value and learn k(F) such that:
            #   s_eff(F) = base * k(F), with s_eff(F45) ~= s45 and s_eff(FBW) ~= sBW.
            try:
                base = float(avgs.get("all", {}).get(ax, 0.0))
            except Exception:
                base = 0.0
            try:
                s45 = float(avgs.get("45lb", {}).get(ax, 0.0))
            except Exception:
                s45 = 0.0
            try:
                sBW = float(avgs.get("bodyweight", {}).get(ax, 0.0))
            except Exception:
                sBW = 0.0
            loads_45 = phase_loads.get("45lb") or []
            loads_bw = phase_loads.get("bodyweight") or []
            F45 = sum(loads_45) / float(len(loads_45)) if loads_45 else 0.0
            FBW = sum(loads_bw) / float(len(loads_bw)) if loads_bw else 0.0
            if base != 0.0:
                k45 = s45 / base
                kBW = sBW / base
            else:
                k45 = 1.0
                kBW = 1.0
            if F45 > 0.0 and FBW > F45:
                b = (kBW - k45) / (FBW - F45)
                a = k45 - b * F45
            else:
                a = 1.0
                b = 0.0
            weight_models[ax] = {
                "base": base,
                "k45": k45,
                "kBW": kBW,
                "F45": F45,
                "FBW": FBW,
                "a": a,
                "b": b,
            }

        self._temp_slopes_by_sensor = slopes_by_sensor
        self._temp_slope_avgs = avgs
        self._temp_slope_stds = stds
        self._temp_weight_models = weight_models

    def _update_temp_slope_panel(self) -> None:
        """Refresh the Temp Slopes tab on the right with current averages/stds."""
        avgs = self._temp_slope_avgs or {}
        stds = getattr(self, "_temp_slope_stds", {}) or {}

        def _get(ph: str, ax: str) -> float:
            try:
                return float(avgs.get(ph, {}).get(ax, 0.0))
            except Exception:
                return 0.0

        def _get_std(ph: str, ax: str) -> float:
            try:
                return float(stds.get(ph, {}).get(ax, 0.0))
            except Exception:
                return 0.0

        # X axis
        try:
            self.lbl_slope_db_x.setText(f"{_get('45lb', 'x'):.6f}")
            self.lbl_std_db_x.setText(f"{_get_std('45lb', 'x'):.6f}")
            self.lbl_slope_bw_x.setText(f"{_get('bodyweight', 'x'):.6f}")
            self.lbl_std_bw_x.setText(f"{_get_std('bodyweight', 'x'):.6f}")
            self.lbl_slope_all_x.setText(f"{_get('all', 'x'):.6f}")
            self.lbl_std_all_x.setText(f"{_get_std('all', 'x'):.6f}")
        except Exception:
            pass
        # Y axis
        try:
            self.lbl_slope_db_y.setText(f"{_get('45lb', 'y'):.6f}")
            self.lbl_std_db_y.setText(f"{_get_std('45lb', 'y'):.6f}")
            self.lbl_slope_bw_y.setText(f"{_get('bodyweight', 'y'):.6f}")
            self.lbl_std_bw_y.setText(f"{_get_std('bodyweight', 'y'):.6f}")
            self.lbl_slope_all_y.setText(f"{_get('all', 'y'):.6f}")
            self.lbl_std_all_y.setText(f"{_get_std('all', 'y'):.6f}")
        except Exception:
            pass
        # Z axis
        try:
            self.lbl_slope_db_z.setText(f"{_get('45lb', 'z'):.6f}")
            self.lbl_std_db_z.setText(f"{_get_std('45lb', 'z'):.6f}")
            self.lbl_slope_bw_z.setText(f"{_get('bodyweight', 'z'):.6f}")
            self.lbl_std_bw_z.setText(f"{_get_std('bodyweight', 'z'):.6f}")
            self.lbl_slope_all_z.setText(f"{_get('all', 'z'):.6f}")
            self.lbl_std_all_z.setText(f"{_get_std('all', 'z'):.6f}")
        except Exception:
            pass

        # Update "Current Plot" table from last plotted adjustment stats
        try:
            base = getattr(self, "_temp_plot_last_base_slope", None)
            mult = getattr(self, "_temp_plot_last_multiplier", None)
            adj = getattr(self, "_temp_plot_last_adj_slope", None)
            imp = getattr(self, "_temp_plot_last_improve_pct", None)
            a = getattr(self, "_temp_plot_last_a", None)
            b = getattr(self, "_temp_plot_last_b", None)
            Fref = getattr(self, "_temp_plot_last_Fref", None)
            is_sum = bool(getattr(self, "_temp_plot_last_is_sum", False))
        except Exception:
            base = mult = adj = imp = a = b = Fref = None
            is_sum = False

        try:
            if base is None:
                self.lbl_plot_base_slope.setText("—")
            else:
                val = float(base)
                if is_sum:
                    val /= 8.0
                self.lbl_plot_base_slope.setText(f"{val:.6f}")
        except Exception:
            pass

        try:
            if mult is None:
                self.lbl_plot_multiplier.setText("—")
            else:
                # Show equation-style: k = a + bF with actual values from last plot
                if a is None or b is None or Fref is None:
                    self.lbl_plot_multiplier.setText(f"k = {float(mult):.4f}")
                else:
                    self.lbl_plot_multiplier.setText(
                        f"k = {float(mult):.4f} = {float(a):.4f} + {float(b):.6f} * {float(Fref):.2f}"
                    )
        except Exception:
            pass

        try:
            if adj is None:
                self.lbl_plot_adj_slope.setText("—")
            else:
                val = float(adj)
                if is_sum:
                    val /= 8.0
                self.lbl_plot_adj_slope.setText(f"{val:.6f}")
        except Exception:
            pass

        try:
            if imp is None:
                self.lbl_plot_improve_pct.setText("—")
            else:
                self.lbl_plot_improve_pct.setText(f"{float(imp):.1f}%")
        except Exception:
            pass

    def _on_plot_discrete_test(self) -> None:
        """Plot temperature vs force for the currently selected discrete test."""
        # Require a selected test path and a plot widget backend
        try:
            test_path = str(getattr(self, "_selected_discrete_test_path", "") or "")
        except Exception:
            test_path = ""
        if not test_path or self._temp_plot_widget is None or self._temp_plot_pg is None:
            return
        import os, csv
        csv_path = os.path.join(test_path, "discrete_temp_session.csv")
        if not os.path.isfile(csv_path) or os.path.getsize(csv_path) <= 0:
            return
        phase_label = str(self.temp_plot_phase_combo.currentText() or "Bodyweight").strip().lower()
        # Map UI label to phase_name in CSV
        if phase_label.startswith("45"):
            phase_name = "45lb"
        else:
            phase_name = "bodyweight"
        sensor_label = str(self.temp_plot_sensor_combo.currentText() or "Sum").strip()
        axis_label = str(self.temp_plot_axis_combo.currentText() or "z").strip().lower()
        if axis_label not in ("x", "y", "z"):
            axis_label = "z"
        # Sensor prefix mapping (must match CSV headers)
        name_map = {
            "Sum": "sum",
            "Rear Right Outer": "rear-right-outer",
            "Rear Right Inner": "rear-right-inner",
            "Rear Left Outer": "rear-left-outer",
            "Rear Left Inner": "rear-left-inner",
            "Front Left Outer": "front-left-outer",
            "Front Left Inner": "front-left-inner",
            "Front Right Outer": "front-right-outer",
            "Front Right Inner": "front-right-inner",
        }
        prefix = name_map.get(sensor_label, "sum")
        col_name = f"{prefix}-{axis_label}"
        xs: list[float] = []
        ys: list[float] = []
        loads_per_sensor: list[float] = []
        # Baseline points to show as faint markers for the CURRENT phase only
        baseline_pts_for_plot: List[Tuple[float, float]] = []
        baseline_low = 74.0
        baseline_high = 78.0
        target_T = 76.0

        # Also load discrete individual measurements if available
        meas_pts_for_plot: List[Tuple[float, float]] = []
        try:
            meas_csv_path = os.path.join(test_path, "discrete_temp_measurements.csv")
            if os.path.isfile(meas_csv_path) and os.path.getsize(meas_csv_path) > 0:
                with open(meas_csv_path, "r", encoding="utf-8", newline="") as mf:
                    # Same logic as main file
                    meas_header_line = mf.readline()
                    if meas_header_line:
                        import io
                        meas_header_reader = csv.reader(io.StringIO(meas_header_line))
                        meas_headers = next(meas_header_reader, [])
                        meas_headers = [h.strip() for h in meas_headers]
                        meas_reader = csv.DictReader(mf, fieldnames=meas_headers, skipinitialspace=True)
                        for row in meas_reader:
                            if not row: continue
                            try:
                                ph = str(row.get("phase_name") or row.get("phase") or "").strip().lower()
                            except Exception: ph = ""
                            if ph != phase_name: continue
                            try:
                                t_val = float(row.get("sum-t") or 0.0)
                                v_val = float(row.get(col_name) or 0.0)
                                meas_pts_for_plot.append((t_val, v_val))
                            except Exception: pass
        except Exception:
            pass

        try:
            with open(csv_path, "r", encoding="utf-8", newline="") as f:
                # Read header line manually to strip whitespace
                header_line = f.readline()
                if not header_line:
                    self._log("plot_test: empty header line")
                    return
                import io
                header_reader = csv.reader(io.StringIO(header_line))
                headers = next(header_reader, [])
                headers = [h.strip() for h in headers]
                # Use skipinitialspace=True to mimic _compute logic and handle value whitespace
                reader = csv.DictReader(f, fieldnames=headers, skipinitialspace=True)

                self._log(f"plot_test: reading {csv_path} phase_name='{phase_name}' col='{col_name}' headers={headers[:5]}...")
                row_count = 0
                match_count = 0
                for row in reader:
                    if not row:
                        continue
                    row_count += 1
                    # Phase for phase-specific regression
                    try:
                        ph = str(row.get("phase_name") or row.get("phase") or "").strip().lower()
                    except Exception:
                        ph = ""
                    # Temperature and value for this sensor/axis
                    try:
                        temp_f = float(row.get("sum-t") or 0.0)
                        y_val = float(row.get(col_name) or 0.0)
                    except Exception:
                        continue
                    # Only use rows matching the selected phase for regression/lines and baseline markers
                    if ph != phase_name:
                        continue
                    match_count += 1
                    # Collect baseline runs for this phase for plotting as faint dots
                    try:
                        if baseline_low <= float(temp_f) <= baseline_high:
                            baseline_pts_for_plot.append((float(temp_f), float(y_val)))
                    except Exception:
                        pass
                    xs.append(temp_f)
                    ys.append(y_val)
                    # Track average per-sensor load for this row.
                    # For Sum plots, use |sum-z|/8.0 to get per-sensor equivalent.
                    # For individual sensors, use that sensor's own |z| value.
                    try:
                        if sensor_label.lower().startswith("sum"):
                            sum_z = float(row.get("sum-z") or 0.0)
                            if sum_z != 0.0:
                                loads_per_sensor.append(abs(sum_z) / 8.0)
                        else:
                            z_col = f"{prefix}-z"
                            fz_sensor = float(row.get(z_col) or 0.0)
                            if fz_sensor != 0.0:
                                loads_per_sensor.append(abs(fz_sensor))
                    except Exception:
                        pass
            self._log(f"plot_test: rows={row_count} matches={match_count} points={len(xs)}")
        except Exception as e:
            self._log(f"plot_test error: {e}")
            xs, ys = [], []
        if not xs or not ys or len(xs) != len(ys):
            self._log("plot_test: no points to plot")
            return

        # Collapse multiple baseline measurements (74–78°F) for this phase into a single
        # weighted-average point for the regression/green series, while keeping all the
        # original baseline runs (any phase) in baseline_pts_for_plot for faint markers.
        try:
            pts_all = list(zip(xs, ys))
            baseline_idx: List[int] = [
                i for i, (t, _y) in enumerate(pts_all) if baseline_low <= float(t) <= baseline_high
            ]
            if len(baseline_idx) > 1:
                # Compute weighted-average baseline point biased toward 76°F
                weights: List[float] = []
                baseline_vals: List[Tuple[float, float]] = []
                for i in baseline_idx:
                    t, yv = pts_all[i]
                    try:
                        dt = abs(float(t) - target_T)
                    except Exception:
                        dt = 0.0
                    w = 1.0 / (1.0 + dt)
                    weights.append(w)
                    baseline_vals.append((float(t), float(yv)))
                w_sum = sum(weights)
                if w_sum > 0.0 and baseline_vals:
                    T0 = sum(w * t for w, (t, _y) in zip(weights, baseline_vals)) / w_sum
                    Y0 = sum(w * y for w, (_t, y) in zip(weights, baseline_vals)) / w_sum
                    # Remove all original baseline points for this phase from the regression set,
                    # then append the single weighted-average baseline point.
                    keep_pts = [p for i, p in enumerate(pts_all) if i not in baseline_idx]
                    keep_pts.append((T0, Y0))
                    xs = [p[0] for p in keep_pts]
                    ys = [p[1] for p in keep_pts]
        except Exception:
            # On any failure, fall back to original points
            pass
        # Sort by temperature ascending for readability
        pts = sorted(zip(xs, ys), key=lambda p: p[0])
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]

        # Use precomputed "all tests" slope for this axis; scale by 8 for Sum plots
        try:
            avgs = getattr(self, "_temp_slope_avgs", {}) or {}
        except Exception:
            avgs = {}
        try:
            m_all = float(avgs.get("all", {}).get(axis_label, 0.0))
        except Exception:
            m_all = 0.0
        m_solid = m_all
        if sensor_label.lower().startswith("sum"):
            m_solid = m_all * 8.0

        # Weight-adjusted slope using simple linear "multiplier vs load" model based on per-sensor load.
        # We start from the all-tests slope for this axis and scale it by k(F_ref).
        try:
            models = getattr(self, "_temp_weight_models", {}) or {}
            m_model = models.get(axis_label, {}) or {}
        except Exception:
            m_model = {}
        try:
            base = float(m_model.get("base", m_all))
        except Exception:
            base = m_all
        try:
            a = float(m_model.get("a", 1.0))
        except Exception:
            a = 1.0
        try:
            b = float(m_model.get("b", 0.0))
        except Exception:
            b = 0.0
        try:
            F45 = float(m_model.get("F45", 0.0))
            FBW = float(m_model.get("FBW", 0.0))
        except Exception:
            F45, FBW = 0.0, 0.0
        F_ref = sum(loads_per_sensor) / float(len(loads_per_sensor)) if loads_per_sensor else 0.0
        if F_ref > 0.0:
            try:
                k_ref = a + b * F_ref
            except Exception:
                k_ref = a
        else:
            k_ref = a
        # Effective per-sensor slope at this reference load
        m_eff_single = base * k_ref
        # For Sum plots, approximate slope as 8x per-sensor slope
        if sensor_label.lower().startswith("sum"):
            m_dashed = m_eff_single * 8.0
        else:
            m_dashed = m_eff_single

        # Compute intercepts that best fit this sensor's data given each fixed slope
        if len(xs) >= 1:
            try:
                mean_t = sum(xs) / float(len(xs))
                mean_y = sum(ys) / float(len(ys))
                b_solid = mean_y - m_solid * mean_t
                b_dashed = mean_y - m_dashed * mean_t
            except Exception:
                b_solid = ys[0]
                b_dashed = ys[0]
        else:
            b_solid = 0.0
            b_dashed = 0.0
        try:
            self._temp_plot_widget.clear()  # type: ignore[union-attr]
            # Re-label axes to reflect the chosen axis
            try:
                axis_label_full = axis_label.upper()
                self._temp_plot_widget.setLabel("bottom", "Temperature (°F)")  # type: ignore[attr-defined]
                self._temp_plot_widget.setLabel("left", f"{sensor_label} {axis_label_full}")  # type: ignore[attr-defined]
            except Exception:
                pass
            # Draw best-fit lines (solid = global slope, dashed = load-adjusted) and data connected with a line
            try:
                solid_ys = [b_solid + m_solid * t for t in xs]
            except Exception:
                solid_ys = ys
            try:
                dashed_ys = [b_dashed + m_dashed * t for t in xs]
            except Exception:
                dashed_ys = ys
            base_color = (180, 180, 255)
            solid_pen = self._temp_plot_pg.mkPen(color=base_color, width=2)  # type: ignore[attr-defined]
            dashed_pen = self._temp_plot_pg.mkPen(color=base_color, width=2, style=QtCore.Qt.DashLine)  # type: ignore[attr-defined]
            # Global all-tests line (solid)
            self._temp_plot_widget.plot(xs, solid_ys, pen=solid_pen)  # type: ignore[attr-defined]
            # Load-adjusted line (dashed)
            self._temp_plot_widget.plot(xs, dashed_ys, pen=dashed_pen)  # type: ignore[attr-defined]

            # Plot individual measurements as faint small dots (more subtle than baseline pts)
            try:
                if meas_pts_for_plot:
                    mx = [float(p[0]) for p in meas_pts_for_plot]
                    my = [float(p[1]) for p in meas_pts_for_plot]
                    self._temp_plot_widget.plot(  # type: ignore[attr-defined]
                        mx,
                        my,
                        pen=None,
                        symbol="o",
                        symbolSize=4,
                        symbolBrush=self._temp_plot_pg.mkBrush(150, 150, 150, 120),
                        symbolPen=None,
                    )
            except Exception:
                pass

            # Plot all baseline runs as semi-transparent, slightly smaller dots (no line)
            try:
                if baseline_pts_for_plot:
                    bx = [float(p[0]) for p in baseline_pts_for_plot]
                    by = [float(p[1]) for p in baseline_pts_for_plot]
                    self._temp_plot_widget.plot(  # type: ignore[attr-defined]
                        bx,
                        by,
                        pen=None,
                        symbol="o",
                        symbolSize=6,
                        symbolBrush=self._temp_plot_pg.mkBrush(180, 180, 255, 90),
                        symbolPen=self._temp_plot_pg.mkPen(color=(180, 180, 255, 60), width=1),
                    )
            except Exception:
                pass

            # Data connected with a lighter line plus markers (including the weighted baseline point)
            self._temp_plot_widget.plot(
                xs,
                ys,
                pen=self._temp_plot_pg.mkPen(color=(120, 220, 120), width=1),  # type: ignore[attr-defined]
                symbol="o",
                symbolBrush=(200, 250, 200),
                symbolSize=8,
            )

            # Compute how much better the adjusted line fits vs base (using SSE)
            try:
                sse_solid = sum((float(y) - float(yb)) ** 2 for y, yb in zip(ys, solid_ys))
                sse_dashed = sum((float(y) - float(yd)) ** 2 for y, yd in zip(ys, dashed_ys))
                if sse_solid > 0.0:
                    improve_pct = (sse_solid - sse_dashed) / sse_solid * 100.0
                else:
                    improve_pct = 0.0
            except Exception:
                improve_pct = 0.0

            # Persist current-plot stats for Temp Slopes tab
            try:
                self._temp_plot_last_base_slope = float(m_solid)
                self._temp_plot_last_multiplier = float(k_ref)
                self._temp_plot_last_adj_slope = float(m_dashed)
                self._temp_plot_last_improve_pct = float(improve_pct)
                self._temp_plot_last_a = float(a)
                self._temp_plot_last_b = float(b)
                self._temp_plot_last_Fref = float(F_ref)
                self._temp_plot_last_is_sum = bool(sensor_label.lower().startswith("sum"))
            except Exception:
                self._temp_plot_last_base_slope = m_solid
                self._temp_plot_last_multiplier = k_ref
                self._temp_plot_last_adj_slope = m_dashed
                self._temp_plot_last_improve_pct = improve_pct
                self._temp_plot_last_a = a
                self._temp_plot_last_b = b
                self._temp_plot_last_Fref = F_ref
                self._temp_plot_last_is_sum = sensor_label.lower().startswith("sum")

            # Also refresh Temp Slopes "Current Plot" table
            try:
                self._update_temp_slope_panel()
            except Exception:
                pass
        except Exception:
            pass
        # Bring Temp Plot tab to front on the left
        try:
            self.top_tabs_left.setCurrentWidget(self.temp_plot_tab)
        except Exception:
            pass

    def _on_discrete_add_existing(self, test_path: str) -> None:
        """Handle Add to Existing Test button selection."""
        if self._live_session is not None:
            try:
                QtWidgets.QMessageBox.information(self, "Discrete Temp Testing", "End the current session before adding to an existing discrete temp test.")
            except Exception:
                pass
            return
        if not test_path:
            return
        try:
            self._log(f"discrete_temp_add_existing: test_path='{test_path}'")
        except Exception:
            pass
        try:
            self._active_discrete_test_path = str(test_path or "")
        except Exception:
            self._active_discrete_test_path = ""

        # Try to load tester/body weight from this test's meta; if available, skip the setup dialog
        tester_override: Optional[str] = None
        bw_override: Optional[float] = None
        try:
            import os, json
            meta_path = os.path.join(test_path, "test_meta.json")
            if os.path.isfile(meta_path):
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f) or {}
                tester_override = str(meta.get("tester_name") or "").strip() or None
                try:
                    bw_val = meta.get("body_weight_n")
                    if bw_val is not None:
                        bw_override = float(bw_val)
                except Exception:
                    bw_override = None
        except Exception:
            tester_override = None
            bw_override = None

        # Start discrete temp live session targeting this existing test folder
        # When adding to an existing discrete session, focus on Plate/Sensor views
        try:
            self.top_tabs_left.setCurrentWidget(self.canvas_left)
        except Exception:
            pass
        try:
            self.top_tabs_right.setCurrentWidget(self._sensor_tab_right)
        except Exception:
            pass

        if tester_override is not None and bw_override is not None:
            self._start_discrete_temp_session(test_path, tester_override, bw_override, skip_dialog=True)
        else:
            # Fallback: ask for session info if meta incomplete
            self._start_discrete_temp_session(test_path)

    def _update_discrete_test_meta(self, test_path: str, tester: str, body_weight_n: float) -> None:
        """Update test_meta.json for a discrete test with tester and body weight."""
        import os, json, time
        try:
            meta_path = os.path.join(test_path, "test_meta.json")
        except Exception:
            return
        meta: dict = {}
        try:
            if os.path.isfile(meta_path):
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f) or {}
        except Exception:
            meta = {}
        # Merge new fields
        try:
            meta["tester_name"] = str(tester or "").strip()
        except Exception:
            meta["tester_name"] = str(tester or "")
        try:
            meta["body_weight_n"] = float(body_weight_n)
        except Exception:
            pass
        if "device_id" not in meta:
            meta["device_id"] = self.state.selected_device_id or ""
        if "short_label" not in meta:
            meta["short_label"] = self._short_device_label(self.state.selected_device_id or "")
        if "date" not in meta:
            try:
                import datetime as _dt
                meta["date"] = _dt.datetime.now().strftime("%m-%d-%Y")
            except Exception:
                pass
        if "created_at_ms" not in meta:
            try:
                meta["created_at_ms"] = int(time.time() * 1000)
            except Exception:
                pass
        try:
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)
        except Exception:
            pass

    def _write_temp_session_meta(
        self,
        device_id: str,
        model_id: str,
        tester: str,
        body_weight_n: float,
        started_at_ms: int,
        is_temp_test: bool,
    ) -> None:
        """Create or update a meta JSON file for a temperature test session next to its CSV."""
        if not is_temp_test:
            return
        try:
            import os, json, datetime, time as _t
        except Exception:
            return
        # Resolve capture directory and base name for this session
        try:
            csv_dir = str(getattr(self, "_capture_csv_dir", "") or "").strip()
        except Exception:
            csv_dir = ""
        try:
            cap_name = str(getattr(self, "_capture_csv_name", "") or "").strip()
        except Exception:
            cap_name = ""
        if not csv_dir or not cap_name:
            return
        try:
            meta_path = os.path.join(csv_dir, f"{cap_name}.meta.json")
        except Exception:
            return

        # Load existing meta if present so we can be additive
        meta: dict = {}
        try:
            if os.path.isfile(meta_path):
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f) or {}
        except Exception:
            meta = {}

        # Basic session identification
        meta["version"] = int(meta.get("version") or 1)
        meta["session_type"] = "temperature_test"
        meta["device_id"] = str(device_id or "")
        meta["model_id"] = str(model_id or "")
        meta["tester_name"] = str(tester or "")
        try:
            meta["body_weight_n"] = float(body_weight_n)
        except Exception:
            pass

        # Reuse short label/date style from discrete temp meta
        if "short_label" not in meta:
            try:
                meta["short_label"] = self._short_device_label(device_id or "")
            except Exception:
                meta["short_label"] = str(device_id or "")
        if "date" not in meta:
            try:
                dt = datetime.datetime.fromtimestamp(int(started_at_ms or int(_t.time() * 1000)) / 1000.0)
                meta["date"] = dt.strftime("%m-%d-%Y")
            except Exception:
                pass

        # Link back to capture on disk
        meta["csv_dir"] = csv_dir
        meta["capture_name"] = cap_name
        try:
            meta["started_at_ms"] = int(started_at_ms)
        except Exception:
            pass

        # Ensure stage_events array exists for later appends
        if not isinstance(meta.get("stage_events"), list):
            meta["stage_events"] = []

        try:
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)
        except Exception:
            pass

    def _append_temp_stage_event(self, stage_name: str, stage_index: int, started_at_ms: int) -> None:
        """Append a stage start event to the temp session meta JSON (if this is a temp test)."""
        try:
            # Only log for temperature test sessions
            if self._live_session is None or not bool(getattr(self._live_session, "is_temp_test", False)):
                return
        except Exception:
            return
        try:
            import os, json
        except Exception:
            return
        try:
            csv_dir = str(getattr(self, "_capture_csv_dir", "") or "").strip()
        except Exception:
            csv_dir = ""
        try:
            cap_name = str(getattr(self, "_capture_csv_name", "") or "").strip()
        except Exception:
            cap_name = ""
        if not csv_dir or not cap_name:
            return
        try:
            meta_path = os.path.join(csv_dir, f"{cap_name}.meta.json")
        except Exception:
            return

        meta: dict = {}
        try:
            if os.path.isfile(meta_path):
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f) or {}
        except Exception:
            meta = {}
        events = meta.get("stage_events")
        if not isinstance(events, list):
            events = []
        try:
            events.append(
                {
                    "stage_index": int(stage_index),
                    "stage_name": str(stage_name or ""),
                    "changed_at_ms": int(started_at_ms),
                }
            )
        except Exception:
            return
        meta["stage_events"] = events
        try:
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)
        except Exception:
            pass

    def _temp_all_cells_done(self) -> bool:
        """Return True if all cells are completed across all stages for a temp test session."""
        try:
            if self._live_session is None or not bool(getattr(self._live_session, "is_temp_test", False)):
                return False
        except Exception:
            return False
        try:
            for st in (self._live_session.stages or []):
                try:
                    total = int(getattr(st, "total_cells", 0))
                except Exception:
                    total = 0
                if total <= 0:
                    continue
                try:
                    completed = sum(
                        1
                        for g in (getattr(st, "results", {}) or {}).values()
                        if g is not None and getattr(g, "fz_mean_n", None) is not None
                    )
                except Exception:
                    completed = 0
                if completed < total:
                    return False
            return True
        except Exception:
            return False

    def _temp_switch_stage_if_possible(self) -> bool:
        """For temp tests, schedule a switch to the other stage if it has remaining cells."""
        try:
            if self._live_session is None or not bool(getattr(self._live_session, "is_temp_test", False)):
                return False
        except Exception:
            return False
        try:
            stages = self._live_session.stages or []
        except Exception:
            return False
        if len(stages) < 2:
            return False
        try:
            cur_idx = int(self._live_stage_idx)
        except Exception:
            cur_idx = 0
        other_idx = 1 if cur_idx == 0 else 0
        try:
            other_stage = stages[other_idx]
        except Exception:
            return False
        # Only consider switch if the other stage still has incomplete cells
        try:
            has_incomplete = any(
                (cell is not None and getattr(cell, "fz_mean_n", None) is None)
                for cell in (getattr(other_stage, "results", {}) or {}).values()
            )
        except Exception:
            has_incomplete = False
        if not has_incomplete:
            return False
        # Defer the actual switch until Fz drops below a safe threshold
        try:
            self._temp_pending_stage_idx = other_idx
        except Exception:
            self._temp_pending_stage_idx = other_idx
        return True

    def _temp_maybe_perform_pending_stage_switch(self, fz_n: float, t_ms: int) -> None:
        """If a temp-test stage switch is pending and Fz is low enough, perform it now."""
        try:
            if self._live_session is None or not bool(getattr(self._live_session, "is_temp_test", False)):
                return
        except Exception:
            return
        try:
            pending_idx = self._temp_pending_stage_idx
        except Exception:
            pending_idx = None
        if pending_idx is None:
            return
        # Require load sufficiently off the plate before switching stages
        try:
            if abs(float(fz_n)) >= 50.0:
                return
        except Exception:
            return
        # Avoid switching while a cell is actively armed/being captured
        if self._active_cell is not None:
            return
        # Perform the switch
        try:
            stages = self._live_session.stages or []
        except Exception:
            stages = []
        if not (0 <= int(pending_idx) < len(stages)):
            try:
                self._temp_pending_stage_idx = None
            except Exception:
                pass
            return
        try:
            stage = stages[int(pending_idx)]
        except Exception:
            return
        try:
            self._live_stage_idx = int(pending_idx)
        except Exception:
            return
        try:
            self._apply_stage_context()
        except Exception:
            pass
        # Log stage switch into temp meta JSON
        try:
            self._append_temp_stage_event(
                stage_name=str(getattr(stage, "name", "")),
                stage_index=int(getattr(stage, "index", 0)),
                started_at_ms=int(t_ms),
            )
        except Exception:
            pass
        # Reset pending state and per-turn counter
        try:
            self._temp_pending_stage_idx = None
        except Exception:
            pass
        try:
            self._temp_cells_this_turn = 0
        except Exception:
            self._temp_cells_this_turn = 0

    def _start_discrete_tare_sequence(self) -> None:
        """Kick off a 15-second settle-and-tare sequence using existing guidance logic."""
        # Use same dialog/countdown as normal, but without periodic scheduling
        try:
            self._tare_active = True
            # Mark that the next successful tare (auto or manual) should be
            # treated as completing this discrete tare sequence.
            try:
                self._discrete_pending_tare_complete = True
            except Exception:
                self._discrete_pending_tare_complete = True
            # Let guidance logic seed countdown and last_tick on first sample
            self._tare_countdown_remaining_s = 0
            self._tare_last_tick_s = None
            self._next_tare_due_ms = None
            self._open_tare_dialog()
        except Exception:
            pass

    def _on_discrete_tare_completed(self) -> None:
        """Advance discrete temp flow after a tare completes."""
        if self._live_session is None or not bool(getattr(self._live_session, "is_discrete_temp", False)):
            return
        # Clear pending flag now that this discrete tare has completed.
        try:
            self._discrete_pending_tare_complete = False
        except Exception:
            self._discrete_pending_tare_complete = False
        # First tare after warmup: mark ready and show circle; do not advance stage
        if not bool(getattr(self, "_discrete_ready_for_data", False)):
            try:
                self._discrete_ready_for_data = True
            except Exception:
                self._discrete_ready_for_data = True
            # Turn model bypass ON for discrete data gathering
            try:
                if callable(getattr(self, "_on_set_model_bypass_cb", None)):
                    self._on_set_model_bypass_cb(True)
                    self._should_revert_bypass = True
                    self._log("discrete_temp: setModelBypass(true) for data gathering")
            except Exception:
                pass
            try:
                self.canvas_left.show_live_center_circle()
                self.canvas_right.show_live_center_circle()
            except Exception:
                pass
            return
        # Subsequent tares: advance to the next stage (if available)
        if self._live_stage_idx + 1 < len(self._live_session.stages):
            self._live_stage_idx += 1
            try:
                self._apply_stage_context()
            except Exception:
                pass

    def _on_discrete_stage_completed(self) -> None:
        """Handle stage completion in discrete temp mode (auto-advance and scheduled tares)."""
        if self._live_session is None or not bool(getattr(self._live_session, "is_discrete_temp", False)):
            return
        idx = int(self._live_stage_idx)
        total_stages = len(self._live_session.stages)
        # After DB stages (0, 2, 4): wait for unload (Fz < 100 N) before advancing/tare
        if idx in (0, 2, 4):
            try:
                self._discrete_waiting_for_unload = True
                self.controls.live_testing_panel.set_debug_status("Remove the load from the plate to continue (Fz < 100 N)…")
            except Exception:
                self._discrete_waiting_for_unload = True
            return
        # After BW stages 1 and 3: run tare sequence before continuing to next DB stage
        if idx in (1, 3):
            self._start_discrete_tare_sequence()
            return
        # After final BW stage 5: finish the session
        if idx == 5:
            try:
                # Before generic summary/cleanup, write discrete session CSV rows
                rows = self._write_discrete_session_csv()
                try:
                    self._discrete_session_success = bool(rows >= 2)
                except Exception:
                    self._discrete_session_success = bool(rows > 0)
                self._submit_results_and_end()
            except Exception:
                self._on_live_end()

    def _accumulate_discrete_measurement(self, phase_kind: str, window_start_ms: int, window_end_ms: int) -> bool:
        """Aggregate detailed sensor data over a stability window for discrete temp sessions.

        Returns True if this window contributed to the per-session stats for the given phase,
        and False if no data was added (e.g., empty window, missing raw samples). This lets
        the caller detect failed captures and keep the user on the current stage.
        """
        try:
            if self._live_session is None or not bool(getattr(self._live_session, "is_discrete_temp", False)):
                return False
            test_path = str(getattr(self, "_active_discrete_test_path", "") or "")
            if not test_path:
                return False
            buf = getattr(self, "_discrete_raw_buffer", None)
            if not isinstance(buf, list) or not buf:
                return False
            dev_id = str(self._live_session.device_id or "").strip()
            if not dev_id:
                return False
            # Filter payloads in window and for current device
            samples: list[dict] = []
            for p in buf:
                try:
                    if not isinstance(p, dict):
                        continue
                    did = str(p.get("deviceId") or p.get("device_id") or "").strip()
                    if did != dev_id:
                        continue
                    t_ms = int(p.get("time") or 0)
                    if t_ms < window_start_ms or t_ms > window_end_ms:
                        continue
                    samples.append(p)
                except Exception:
                    continue
            if not samples:
                try:
                    self._log(f"discrete_accum: no samples in window [{window_start_ms},{window_end_ms}] phase={phase_kind}")
                except Exception:
                    pass
                return False
            n = len(samples)
            # Column layout for CSV
            cols = [
                "time", "phase", "device_id", "phase_name", "phase_id", "record_id",
                "rear-right-outer-x", "rear-right-outer-y", "rear-right-outer-z", "rear-right-outer-t",
                "rear-right-inner-x", "rear-right-inner-y", "rear-right-inner-z", "rear-right-inner-t",
                "rear-left-outer-x", "rear-left-outer-y", "rear-left-outer-z", "rear-left-outer-t",
                "rear-left-inner-x", "rear-left-inner-y", "rear-left-inner-z", "rear-left-inner-t",
                "front-left-outer-x", "front-left-outer-y", "front-left-outer-z", "front-left-outer-t",
                "front-left-inner-x", "front-left-inner-y", "front-left-inner-z", "front-left-inner-t",
                "front-right-outer-x", "front-right-outer-y", "front-right-outer-z", "front-right-outer-t",
                "front-right-inner-x", "front-right-inner-y", "front-right-inner-z", "front-right-inner-t",
                "sum-x", "sum-y", "sum-z", "sum-t",
                "moments-x", "moments-y", "moments-z",
                "COPx", "COPy",
                "bx", "by", "bz", "mx", "my", "mz",
            ]
            # Sensor name -> CSV prefix
            name_map = {
                "Rear Right Outer": "rear-right-outer",
                "Rear Right Inner": "rear-right-inner",
                "Rear Left Outer": "rear-left-outer",
                "Rear Left Inner": "rear-left-inner",
                "Front Left Outer": "front-left-outer",
                "Front Left Inner": "front-left-inner",
                "Front Right Outer": "front-right-outer",
                "Front Right Inner": "front-right-inner",
                "Sum": "sum",
            }
            # Accumulators
            sums: dict[str, float] = {c: 0.0 for c in cols}
            last_record_id: int = 0
            for p in samples:
                try:
                    rec_id = int(p.get("recordId") or p.get("record_id") or 0)
                    if rec_id:
                        last_record_id = rec_id
                except Exception:
                    pass
                try:
                    avg_temp = float(p.get("avgTemperatureF") or 0.0)
                except Exception:
                    avg_temp = 0.0
                sensors = p.get("sensors") or []
                # Map sensors by name
                by_name: dict[str, dict] = {}
                for s in sensors:
                    try:
                        nm = str((s or {}).get("name") or "").strip()
                    except Exception:
                        nm = ""
                    if nm:
                        by_name[nm] = s
                for nm, prefix in name_map.items():
                    s = by_name.get(nm)
                    if not isinstance(s, dict):
                        continue
                    try:
                        x = float(s.get("x") or 0.0)
                        y = float(s.get("y") or 0.0)
                        z = float(s.get("z") or 0.0)
                    except Exception:
                        x = y = z = 0.0
                    # Vector used as per-sensor temperature proxy
                    t = avg_temp
                    sums[f"{prefix}-x"] = sums.get(f"{prefix}-x", 0.0) + x
                    sums[f"{prefix}-y"] = sums.get(f"{prefix}-y", 0.0) + y
                    sums[f"{prefix}-z"] = sums.get(f"{prefix}-z", 0.0) + z
                    sums[f"{prefix}-t"] = sums.get(f"{prefix}-t", 0.0) + t
                # Moments
                m = p.get("moments") or {}
                try:
                    sums["moments-x"] += float(m.get("x") or 0.0)
                    sums["moments-y"] += float(m.get("y") or 0.0)
                    sums["moments-z"] += float(m.get("z") or 0.0)
                except Exception:
                    pass
                # COP
                cop = p.get("cop") or {}
                try:
                    sums["COPx"] += float(cop.get("x") or 0.0)
                    sums["COPy"] += float(cop.get("y") or 0.0)
                except Exception:
                    pass
            # Build single-measurement row (averages)
            row: dict[str, object] = {}
            # Time: consistent per session (use session start)
            try:
                row["time"] = int(getattr(self, "_discrete_session_start_ms", window_start_ms))
            except Exception:
                row["time"] = int(window_start_ms)
            phase_name = "45lb" if phase_kind == "45lb" else "bodyweight"
            row["phase"] = phase_name
            row["phase_name"] = phase_name
            row["phase_id"] = phase_name
            row["device_id"] = dev_id
            row["record_id"] = int(last_record_id)
            # Averages
            for key, total in sums.items():
                if key in ("time", "phase", "phase_name", "phase_id", "device_id", "record_id"):
                    continue
                if n > 0:
                    row[key] = float(total) / float(n)
            # Initialize missing numeric fields to 0 for consistency
            for key in cols:
                if key not in row:
                    if key in ("time", "phase", "phase_name", "phase_id", "device_id"):
                        continue
                    row.setdefault(key, 0.0)

            # Save this individual measurement (non-averaged) to a separate CSV
            try:
                import csv
                import os
                if test_path:
                    meas_csv_path = os.path.join(test_path, "discrete_temp_measurements.csv")
                    write_header = not os.path.isfile(meas_csv_path) or os.path.getsize(meas_csv_path) == 0

                    with open(meas_csv_path, "a", newline="", encoding="utf-8") as f:
                        writer = csv.writer(f)
                        if write_header:
                            writer.writerow(cols)

                        # Extract values in the correct column order
                        vals = []
                        for c in cols:
                            vals.append(row.get(c, 0.0))
                        writer.writerow(vals)
            except Exception:
                pass

            # Fold into per-session stats (running average over up to 3 measurements)
            stats = getattr(self, "_discrete_session_stats", None)
            if not isinstance(stats, dict):
                return False
            bucket = stats.get(phase_kind)
            if not isinstance(bucket, dict):
                return False
            cnt = int(bucket.get("count") or 0)
            if cnt <= 0:
                bucket["row"] = row
                bucket["count"] = 1
                try:
                    self._log(f"discrete_accum: init phase={phase_kind} window=[{window_start_ms},{window_end_ms}] n={n}")
                except Exception:
                    pass
            else:
                prev = bucket.get("row") or {}
                merged: dict[str, object] = dict(prev)
                for key in cols:
                    if key in ("time", "phase", "phase_name", "phase_id", "device_id"):
                        merged[key] = prev.get(key, row.get(key))
                        continue
                    try:
                        v_prev = float(prev.get(key, 0.0))
                        v_new = float(row.get(key, 0.0))
                        merged[key] = (v_prev * cnt + v_new) / float(cnt + 1)
                    except Exception:
                        merged[key] = prev.get(key, row.get(key))
                bucket["row"] = merged
                bucket["count"] = cnt + 1
                try:
                    self._log(f"discrete_accum: update phase={phase_kind} count={cnt+1}")
                except Exception:
                    pass
            return True
        except Exception:
            return False

    def _write_discrete_session_csv(self) -> int:
        """Write two rows (45lb/bodyweight) for the current discrete session into discrete_temp_session.csv."""
        rows_written = 0
        try:
            if self._live_session is None or not bool(getattr(self._live_session, "is_discrete_temp", False)):
                return 0
            test_path = str(getattr(self, "_active_discrete_test_path", "") or "")
            if not test_path:
                return 0
            import os, csv, time as _tmod
            csv_path = os.path.join(test_path, "discrete_temp_session.csv")
            cols = [
                "time", "phase", "device_id", "phase_name", "phase_id", "record_id",
                "rear-right-outer-x", "rear-right-outer-y", "rear-right-outer-z", "rear-right-outer-t",
                "rear-right-inner-x", "rear-right-inner-y", "rear-right-inner-z", "rear-right-inner-t",
                "rear-left-outer-x", "rear-left-outer-y", "rear-left-outer-z", "rear-left-outer-t",
                "rear-left-inner-x", "rear-left-inner-y", "rear-left-inner-z", "rear-left-inner-t",
                "front-left-outer-x", "front-left-outer-y", "front-left-outer-z", "front-left-outer-t",
                "front-left-inner-x", "front-left-inner-y", "front-left-inner-z", "front-left-inner-t",
                "front-right-outer-x", "front-right-outer-y", "front-right-outer-z", "front-right-outer-t",
                "front-right-inner-x", "front-right-inner-y", "front-right-inner-z", "front-right-inner-t",
                "sum-x", "sum-y", "sum-z", "sum-t",
                "moments-x", "moments-y", "moments-z",
                "COPx", "COPy",
                "bx", "by", "bz", "mx", "my", "mz",
            ]
            stats = getattr(self, "_discrete_session_stats", None)
            if not isinstance(stats, dict):
                return 0
            rows_to_append: list[list[object]] = []
            for phase_kind in ("45lb", "bodyweight"):
                bucket = stats.get(phase_kind)
                if not isinstance(bucket, dict):
                    continue
                if int(bucket.get("count") or 0) <= 0:
                    continue
                row = bucket.get("row") or {}
                # Ensure required meta fields
                try:
                    row["device_id"] = row.get("device_id") or (self._live_session.device_id or "")
                except Exception:
                    row["device_id"] = self._live_session.device_id or ""
                phase_name = "45lb" if phase_kind == "45lb" else "bodyweight"
                row["phase"] = phase_name
                row["phase_name"] = phase_name
                row["phase_id"] = phase_name
                if "time" not in row:
                    try:
                        row["time"] = int(getattr(self, "_discrete_session_start_ms", int(_tmod.time() * 1000)))
                    except Exception:
                        row["time"] = int(_tmod.time() * 1000)
                data_row: list[object] = []
                for c in cols:
                    v = row.get(c)
                    data_row.append(v)
                rows_to_append.append(data_row)
            if not rows_to_append:
                try:
                    self._log("discrete_csv: no rows to append for this session (stats empty)")
                except Exception:
                    pass
                return 0
            # Write header if file is new/empty, then append rows
            write_header = not os.path.isfile(csv_path) or os.path.getsize(csv_path) == 0
            with open(csv_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if write_header:
                    writer.writerow(cols)
                for r in rows_to_append:
                    writer.writerow(r)
            rows_written = len(rows_to_append)
            try:
                self._log(f"discrete_csv: wrote {rows_written} rows to {csv_path}")
            except Exception:
                pass
        except Exception:
            rows_written = 0
        return rows_written

    def _discrete_xy_stable(self) -> bool:
        """Check COP stability during the Fz stability window for discrete temp sessions.

        Requirement: during the current stability window, the COP must stay within a
        3 cm radius (30 mm) of its mean position.
        """
        try:
            if self._live_session is None or not bool(getattr(self._live_session, "is_discrete_temp", False)):
                return True
            samples = getattr(self, "_recent_samples", None)
            if not isinstance(samples, list) or not samples:
                return True
            xs = [float(s[1]) for s in samples]
            ys = [float(s[2]) for s in samples]
            if not xs or not ys:
                return True
            import math as _math
            mean_x = sum(xs) / float(len(xs))
            mean_y = sum(ys) / float(len(ys))
            max_r_mm = 20.0  # 2 cm radius
            max_seen = 0.0
            for x, y in zip(xs, ys):
                dx = x - mean_x
                dy = y - mean_y
                r = _math.sqrt(dx * dx + dy * dy)
                if r > max_r_mm:
                    max_seen = max(max_seen, r)
                    try:
                        self._log(
                            f"discrete_xy_stab: COP unstable (r={r:.1f} mm > {max_r_mm:.1f} mm); max_seen={max_seen:.1f}"
                        )
                    except Exception:
                        pass
                    return False
                max_seen = max(max_seen, r)
            try:
                self._log(
                    f"discrete_xy_stab: COP stable; max_radius={max_seen:.1f} mm (limit={max_r_mm:.1f} mm)"
                )
            except Exception:
                pass
            return True
        except Exception:
            return True

    def _start_discrete_temp_session(self, test_path: str, tester_override: Optional[str] = None, body_weight_n_override: Optional[float] = None, skip_dialog: bool = False) -> None:
        """Start a discrete temperature live session: single center location, 6 phases (3x DB, 3x BW)."""
        # Guard: only allow in single-device mode with a selected device
        if self.state.display_mode != "single" or not (self.state.selected_device_id or "").strip():
            try:
                QtWidgets.QMessageBox.warning(self, "Discrete Temp Testing", "Select a single plate in Config before starting a discrete temp session.")
            except Exception:
                pass
            return
        dev_id = self.state.selected_device_id or ""
        model_id = self.state.selected_device_type or "06"
        self._log(f"discrete_temp_start: device_id={dev_id}, model_id={model_id}, test_path='{test_path}'")

        # Capture tester/body weight either from overrides or from the setup dialog
        if skip_dialog and tester_override is not None and body_weight_n_override is not None:
            tester = str(tester_override or "").strip()
            bw_n = float(body_weight_n_override)
            self._log(f"discrete_setup_values(meta): tester='{tester}', model_id={model_id}, device_id={dev_id}, bw_n={bw_n:.1f}")
        else:
            dlg = LiveTestSetupDialog(self, is_temp_test=True)
            dlg.set_device_info(dev_id, model_id)
            dlg.set_defaults(tester=tester_override or "", body_weight_n=float(body_weight_n_override or 0.0))
            result = dlg.exec()
            self._log(f"discrete_setup_dialog_result: {result}")
            if result != LiveTestSetupDialog.Accepted:
                self._log("discrete_temp_start: dialog cancelled")
                return
            tester, bw_n, _is_temp, _do_capture, _save_dir = dlg.get_values()
            self._log(f"discrete_setup_values: tester='{tester}', model_id={model_id}, device_id={dev_id}, bw_n={bw_n:.1f}")

        # If this test folder is a legacy date-only folder, move contents into a per-tester subfolder
        try:
            import os, shutil, re
            tester_norm = self._normalize_tester_folder(tester)
            # Detect if test_path already has a tester subfolder structure
            has_meta_here = os.path.isfile(os.path.join(test_path, "test_meta.json"))
            has_subdirs = any(os.path.isdir(os.path.join(test_path, d)) for d in os.listdir(test_path))
            
            # Check if we are likely already in a tester subfolder (parent is a date folder)
            parent_dir_name = os.path.basename(os.path.dirname(os.path.abspath(test_path)))
            parent_is_date = bool(re.match(r"^\d{1,2}-\d{1,2}-\d{4}$", parent_dir_name))
            
            # Check if current folder name matches the tester (normalized)
            current_is_tester = os.path.basename(test_path).lower() == tester_norm
            
            if has_meta_here and not has_subdirs and not parent_is_date and not current_is_tester:
                # Legacy layout: discrete_temp_testing/<plate>/<date> -> move into <date>/<tester_norm>
                date_dir = test_path
                target_dir = os.path.join(date_dir, tester_norm)
                if os.path.abspath(target_dir) != os.path.abspath(date_dir):
                    os.makedirs(target_dir, exist_ok=True)
                    for name in os.listdir(date_dir):
                        src = os.path.join(date_dir, name)
                        # Skip existing subdirs (should not exist in legacy), move files only
                        if os.path.isdir(src):
                            continue
                        dst = os.path.join(target_dir, name)
                        try:
                            shutil.move(src, dst)
                        except Exception:
                            pass
                test_path = target_dir
        except Exception:
            pass

        # Ensure active path tracks the canonical test folder (date/tester)
        try:
            self._active_discrete_test_path = str(test_path or "")
        except Exception:
            self._active_discrete_test_path = test_path

        # Persist tester metadata into this test's meta file
        try:
            self._update_discrete_test_meta(test_path, tester, float(bw_n))
        except Exception:
            pass

        # Build thresholds similar to live testing
        db_tol_n = float(config.THRESHOLDS_DB_N_BY_MODEL.get(model_id, 6.0))
        try:
            bw_pct = float(getattr(config, "THRESHOLDS_BW_PCT_BY_MODEL", {}).get(model_id, 0.01))
        except Exception:
            bw_pct = 0.01
        bw_tol_n = round(float(bw_n) * float(bw_pct), 1)
        thresholds = Thresholds(
            dumbbell_tol_n=db_tol_n,
            bodyweight_tol_n=bw_tol_n,
        )

        # Discrete temp grid: single logical cell at center
        rows, cols = 1, 1
        from ..live_testing_model import LiveTestSession, LiveTestStage, GridCellResult, Thresholds as _T  # noqa: F401

        session = LiveTestSession(
            tester_name=tester,
            device_id=dev_id,
            model_id=model_id,
            body_weight_n=bw_n,
            thresholds=thresholds,
            grid_rows=rows,
            grid_cols=cols,
            is_temp_test=True,
            is_discrete_temp=True,
        )

        # Build 6 phases: DB/BW repeated 3x at center
        import math
        lb_to_n = 4.44822
        names = [
            "45 lb DB (1/3)",
            "Body Weight (1/3)",
            "45 lb DB (2/3)",
            "Body Weight (2/3)",
            "45 lb DB (3/3)",
            "Body Weight (3/3)",
        ]
        targets = [
            45 * lb_to_n,
            bw_n,
            45 * lb_to_n,
            bw_n,
            45 * lb_to_n,
            bw_n,
        ]
        stage_idx = 1
        for name, tgt in zip(names, targets):
            stage = LiveTestStage(
                index=stage_idx,
                name=name,
                location="Center",
                target_n=float(tgt),
                total_cells=1,
            )
            # Single logical cell (0,0)
            stage.results[(0, 0)] = GridCellResult(row=0, col=0)
            session.stages.append(stage)
            stage_idx += 1

        self._live_session = session
        self._live_stage_idx = 0
        self._active_cell = None
        self._recent_samples.clear()
        # Reset arming state
        self._arming_cell = None
        self._arming_start_ms = None
        # Discrete-specific flow flags
        self._discrete_tare_mode = True
        self._discrete_ready_for_data = False
        self._discrete_waiting_for_unload = False
        # Track per-session aggregation for CSV (one 45lb row, one bodyweight row)
        import time as _tmod
        try:
            self._discrete_session_start_ms = int(_tmod.time() * 1000)
        except Exception:
            self._discrete_session_start_ms = 0
        self._discrete_session_stats = {
            "45lb": {"count": 0, "row": {}},
            "bodyweight": {"count": 0, "row": {}},
        }
        self._discrete_session_success: bool = False
        # Raw payload buffer for discrete averaging
        self._discrete_raw_buffer: list[dict] = []
        # Discrete-specific flow flags
        self._discrete_tare_mode = True
        self._discrete_ready_for_data = False
        # Ensure model bypass is OFF during warmup/tare and set captureDetail for temp
        try:
            if callable(getattr(self, "_on_set_model_bypass_cb", None)):
                self._on_set_model_bypass_cb(False)
                self._should_revert_bypass = False
                self._log("discrete_temp: setModelBypass(false) before warmup")
        except Exception:
            pass
        try:
            if callable(getattr(self, "_on_update_dynamo_config_cb", None)):
                self._on_update_dynamo_config_cb("captureDetail", "allTemp")
                self._log("discrete_temp: captureDetail set to 'allTemp'")
        except Exception:
            pass

        # Clear any previous colors and wire up controls; use real stage context for labels.
        try:
            self.canvas_left.clear_live_colors()
            self.canvas_right.clear_live_colors()
        except Exception:
            pass
        try:
            self.controls.live_testing_panel.btn_start.setEnabled(False)
            self.controls.live_testing_panel.btn_end.setEnabled(True)
            try:
                # Navigation buttons are not used in discrete mode
                self.controls.live_testing_panel.btn_next.setEnabled(False)
                self.controls.live_testing_panel.btn_prev.setEnabled(False)
            except Exception:
                pass
            self.controls.live_testing_panel.set_next_stage_label("Next Stage")
            self.controls.live_testing_panel.set_metadata(tester, dev_id, model_id, bw_n)
            self.controls.live_testing_panel.set_thresholds(thresholds.dumbbell_tol_n, thresholds.bodyweight_tol_n)
            # Use the current stage object so the helper label always matches the actual stage
            self._apply_stage_context()
            self._log(
                f"discrete_session_initialized: rows={rows}, cols={cols}, stages={len(self._live_session.stages)} path='{test_path}'"
            )
        except Exception:
            pass

        # Warm-up sequence (20 s) before first tare
        try:
            warm = WarmupPromptDialog(self, duration_s=20)
            res = warm.exec()
        except Exception:
            res = 0
        if res != WarmupPromptDialog.Accepted:
            self._log("discrete_temp_start: warmup cancelled")
            return
        # Immediately run first tare guidance (15 s settle) before Stage 1
        self._start_discrete_tare_sequence()

    # Live Testing session handlers
    def _on_live_start(self) -> None:
        # Guard: only allow in single-device mode with a selected device
        if self.state.display_mode != "single" or not (self.state.selected_device_id or "").strip():
            try:
                QtWidgets.QMessageBox.warning(self, "Live Testing", "Select a single plate in Config before starting a session.")
            except Exception:
                pass
            self._update_live_start_enabled()
            return
        self._log("start_session: opening setup dialog")
        # Gather device and model
        dev_id = self.state.selected_device_id or ""
        model_id = self.state.selected_device_type or "06"
        # Session mode (Normal vs Temperature Test) comes from the Live Testing panel dropdown.
        is_temp_test = False
        try:
            panel = self.controls.live_testing_panel
            if hasattr(panel, "is_temperature_session"):
                is_temp_test = bool(panel.is_temperature_session())
        except Exception:
            pass
        dlg = LiveTestSetupDialog(self, is_temp_test=is_temp_test)
        dlg.set_device_info(dev_id, model_id)
        dlg.set_defaults(tester="", body_weight_n=0.0)
        result = dlg.exec()
        self._log(f"setup_dialog_result: {result}")
        if result != LiveTestSetupDialog.Accepted:
            self._log("start_session: dialog cancelled")
            return
        tester, bw_n, is_temp_test, do_capture, save_dir = dlg.get_values()
        self._log(f"setup_values: tester='{tester}', model_id={model_id}, device_id={dev_id}, bw_n={bw_n:.1f}, temp={is_temp_test}, capture={do_capture}")

        # If capture selected, configure backend capture settings prior to session
        if do_capture:
            try:
                if callable(self._on_update_dynamo_config_cb):
                    import os as _os
                    self._on_update_dynamo_config_cb("autoSaveCsvs", True)
                    # Ensure per-device directories exist in both categories
                    try:
                        # Compute project root from this file (src/ui -> src -> root)
                        _root = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", ".."))
                        # Use real device id in folder name (allow '.', '-', '_')
                        dev_norm = "".join(ch for ch in (dev_id or "") if ch.isalnum() or ch in (".", "-", "_"))
                        base_temp = _os.path.join(_root, "temp_testing")
                        base_live = _os.path.join(_root, "live_test_logs")
                        temp_dir = _os.path.join(base_temp, dev_norm) if dev_norm else base_temp
                        live_dir = _os.path.join(base_live, dev_norm) if dev_norm else base_live
                        try:
                            _os.makedirs(temp_dir, exist_ok=True)
                        except Exception:
                            pass
                        try:
                            _os.makedirs(live_dir, exist_ok=True)
                        except Exception:
                            pass
                        # Resolve directory to use for this session: user choice or default
                        resolved_dir_in = (save_dir or "").strip()
                        # If user picked a base folder (without device subfolder), append device folder
                        if not resolved_dir_in:
                            resolved_dir = temp_dir if is_temp_test else live_dir
                        else:
                            norm_in = _os.path.normpath(resolved_dir_in)
                            if dev_norm:
                                if _os.path.normpath(norm_in) == _os.path.normpath(base_temp) and is_temp_test:
                                    resolved_dir = _os.path.join(base_temp, dev_norm)
                                elif _os.path.normpath(norm_in) == _os.path.normpath(base_live) and not is_temp_test:
                                    resolved_dir = _os.path.join(base_live, dev_norm)
                                else:
                                    resolved_dir = resolved_dir_in
                            else:
                                resolved_dir = resolved_dir_in
                        # Ensure final directory exists
                        try:
                            _os.makedirs(resolved_dir, exist_ok=True)
                        except Exception:
                            pass
                        self._on_update_dynamo_config_cb("csvSaveDirectory", str(resolved_dir))
                    except Exception:
                        pass
                    self._on_update_dynamo_config_cb("captureDetail", "allTemp")
                    self._on_update_dynamo_config_cb("captureDetailRatio", 1)
                    self._on_update_dynamo_config_cb("normalizeData", False)
                # No longer managing model bypass from session setup; backend handles it
            except Exception:
                pass
        # Grid dimensions driven by config (canonical device space)
        rows, cols = config.GRID_DIMS_BY_MODEL.get(model_id, (3, 3))

        # Load per-model thresholds from config
        # DB uses fixed N; BW uses percentage of body weight, displayed/used as rounded N
        db_tol_n = float(config.THRESHOLDS_DB_N_BY_MODEL.get(model_id, 6.0))
        try:
            bw_pct = float(getattr(config, "THRESHOLDS_BW_PCT_BY_MODEL", {}).get(model_id, 0.01))
        except Exception:
            bw_pct = 0.01
        bw_tol_n = round(float(bw_n) * float(bw_pct), 1)
        thresholds = Thresholds(
            dumbbell_tol_n=db_tol_n,
            bodyweight_tol_n=bw_tol_n,
        )
        session = LiveTestSession(
            tester_name=tester,
            device_id=dev_id,
            model_id=model_id,
            body_weight_n=bw_n,
            thresholds=thresholds,
            grid_rows=rows,
            grid_cols=cols,
            is_temp_test=bool(is_temp_test),
        )

        # Build stages
        import math
        lb_to_n = 4.44822
        if is_temp_test:
            # Temperature Test: two phases total — 45 lb DB and two-leg Body Weight
            names = ["45 lb DB", "Body Weight"]
            targets = [45 * lb_to_n, bw_n]
            stage_idx = 1
            for i in range(len(names)):
                stage = LiveTestStage(index=stage_idx, name=names[i], location="A", target_n=targets[i], total_cells=rows * cols)
                for r in range(rows):
                    for c in range(cols):
                        stage.results[(r, c)] = GridCellResult(row=r, col=c)
                session.stages.append(stage)
                stage_idx += 1
        else:
            # Default: 6 stages (A/B) x (DB, BW, BW-one-foot)
            targets = [45 * lb_to_n, bw_n, bw_n]  # DB, BW, BW-one-foot (full BW)
            names = ["45 lb DB", "Body Weight", "Body Weight One Foot"]
            stage_idx = 1
            for location in ("A", "B"):
                for i in range(3):
                    stage = LiveTestStage(index=stage_idx, name=names[i], location=location, target_n=targets[i], total_cells=rows * cols)
                    # Prepopulate result slots
                    for r in range(rows):
                        for c in range(cols):
                            stage.results[(r, c)] = GridCellResult(row=r, col=c)
                    session.stages.append(stage)
                    stage_idx += 1

        self._live_session = session
        self._live_stage_idx = 0
        self._active_cell = None
        self._recent_samples.clear()
        # Initialize next tare time
        try:
            self._next_tare_due_ms = 0  # trigger asap after first few frames
        except Exception:
            self._next_tare_due_ms = None
        try:
            self.canvas_left.clear_live_colors()
            self.canvas_right.clear_live_colors()
        except Exception:
            pass
        # Reflect metadata in both panels
        try:
            self.controls.live_testing_panel.btn_start.setEnabled(False)
            self.controls.live_testing_panel.btn_end.setEnabled(True)
            # Navigation buttons active during a session
            try:
                self.controls.live_testing_panel.btn_next.setEnabled(True)
                self.controls.live_testing_panel.btn_prev.setEnabled(False)
            except Exception:
                pass
            self.controls.live_testing_panel.set_next_stage_label("Next Stage")
            self.controls.live_testing_panel.set_metadata(tester, dev_id, model_id, bw_n)
            self.controls.live_testing_panel.set_thresholds(thresholds.dumbbell_tol_n, thresholds.bodyweight_tol_n)
            self.canvas_left.show_live_grid(rows, cols)
            self.canvas_right.show_live_grid(rows, cols)
            self.controls.live_testing_panel.set_stage_progress("Stage 1: 45 lb DB @ A", 0, rows * cols)
            # Request current model metadata for this device and reflect in panel
            try:
                self.controls.live_testing_panel.set_current_model("Loading…")
                # Also reflect Loading… in Session Info pane's Model ID until fetched
                self.controls.live_testing_panel.set_session_model_id("Loading…")
            except Exception:
                pass
            if self._request_model_metadata_cb and isinstance(dev_id, str) and dev_id.strip():
                try:
                    # Reset active model label until metadata arrives
                    self._active_model_label = None
                    self._request_model_metadata_cb(dev_id)
                except Exception:
                    pass
            # Apply initial stage context so Temperature Test shows its banner from the start
            try:
                self._apply_stage_context()
            except Exception:
                pass
            self._log(f"session_initialized: rows={rows}, cols={cols}, stages={len(self._live_session.stages)}")
        except Exception:
            pass
        # Persist minimal metadata for future temperature testing grading
        try:
            import time as _t
            started_ms = int(_t.time() * 1000)
            # Best-effort: store resolved CSV dir and default capture name if computed later
            csv_dir = ""
            try:
                csv_dir = str(getattr(self, "_capture_csv_dir", "") or "")
            except Exception:
                csv_dir = ""
            cap_name = ""
            try:
                cap_name = str(getattr(self, "_capture_csv_name", "") or "")
            except Exception:
                cap_name = ""
            meta_store.insert_live_session_meta(
                device_id=dev_id,
                model_id=model_id,
                tester=tester,
                body_weight_n=bw_n,
                capture_name=cap_name,
                csv_dir=csv_dir,
                started_at_ms=started_ms,
            )
            # Initialize stage mark pending; first snapshot records start
            try:
                self._stage_mark_active_idx = 1
                self._stage_mark_pending_start = True
                self._log(f"stage_mark_init: idx=1 pending_start=True")
            except Exception:
                pass
        except Exception:
            pass

        # Start capture if requested
        if do_capture:
            try:
                group_id = ""
                try:
                    # Prefer backend-resolved group for selected device
                    if callable(self._resolve_group_id_cb):
                        group_id = str(self._resolve_group_id_cb(dev_id) or "").strip()
                    # Fallback to manual entry
                    if not group_id:
                        group_id = self.controls.group_edit.text().strip()
                    # If still empty during this live/temperature test, default to device id
                    # This mirrors backend behavior where single-device groups often use
                    # the device axf_id as the group axf_id (e.g., "07.00000051").
                    if not group_id and is_temp_test and dev_id:
                        group_id = str(dev_id).strip()
                except Exception:
                    group_id = ""
                self._capture_group_id = group_id or ""
                # Build capture start payload compatible with Controller.start_capture
                # Use 'simple' configuration by default
                from datetime import datetime
                ts = datetime.now().strftime("%Y%m%d-%H%M%S")
                # Include device id (sanitized) in captureName
                def _sanitize(s: str) -> str:
                    try:
                        # Allow '.' in capture name to preserve real device id format
                        return "".join(ch for ch in s if (ch.isalnum() or ch in (".", "-", "_")))
                    except Exception:
                        return s
                dev_part = _sanitize(dev_id or "")
                default_name = f"{'temp' if is_temp_test else 'live'}-raw-{dev_part}-{ts}" if dev_part else f"{'temp' if is_temp_test else 'live'}-raw-{ts}"
                payload = {
                    "capture_name": default_name,
                    "capture_configuration": "simple",
                    "group_id": self._capture_group_id,
                    "athlete_id": "",
                    "tags": ["raw", "full-detail"],
                }
                # Remember capture file target for post-processing
                try:
                    self._capture_csv_dir = str(resolved_dir)
                    self._capture_csv_name = str(default_name)
                    self._capture_device_id_real = str(dev_id or "")
                except Exception:
                    pass

                # Initialize temp-testing session meta JSON next to the capture CSV
                if is_temp_test:
                    try:
                        import time as _t
                        started_ms_for_meta = int(_t.time() * 1000)
                    except Exception:
                        started_ms_for_meta = 0
                    try:
                        self._write_temp_session_meta(
                            device_id=dev_id,
                            model_id=model_id,
                            tester=tester,
                            body_weight_n=bw_n,
                            started_at_ms=started_ms_for_meta,
                            is_temp_test=bool(is_temp_test),
                        )
                    except Exception:
                        pass

                # Reuse ControlPanel signal so Controller handles wiring
                self.controls.start_capture_requested.emit(payload)
                self._capture_active = True
                self._log(f"capture_started: group_id='{self._capture_group_id}' name='{default_name}'")
            except Exception:
                pass

        # If temperature test, ensure temperature correction is turned off on backend
        try:
            if is_temp_test and callable(self._on_update_dynamo_config_cb):
                self._on_update_dynamo_config_cb("useTemperatureCorrection", False)
                if callable(self._on_apply_temp_corr_cb):
                    self._on_apply_temp_corr_cb()
                self._log("temp_test: disabled temperature correction (applied)")
        except Exception:
            pass

        # Temperature Test: run warm-up and an initial auto-tare sequence before measurements
        if is_temp_test:
            # Warm-up sequence (20 s) before initial tare, mirroring discrete temp behavior
            try:
                # Mark warmup as active so tare guidance does not show its dialog yet
                self._temp_warmup_active = True
                warm = WarmupPromptDialog(self, duration_s=20)
                res = warm.exec()
            except Exception:
                res = 0
            if res != WarmupPromptDialog.Accepted:
                try:
                    self._log("temp_test: warmup cancelled")
                except Exception:
                    pass
                try:
                    self._temp_warmup_active = False
                except Exception:
                    self._temp_warmup_active = False
                self._on_live_end()
                return
            # Kick off initial tare guidance: 15 s under threshold before auto-tare
            try:
                self._temp_warmup_active = False
                self._tare_active = True
                self._tare_countdown_remaining_s = 0
                self._tare_last_tick_s = None
                self._next_tare_due_ms = None
                self._open_tare_dialog()
            except Exception:
                pass

    def _on_next_stage(self) -> None:
        if self._live_session is None:
            return
        # Free navigation: always advance when possible
        if self._live_stage_idx + 1 < len(self._live_session.stages):
            self._live_stage_idx += 1
            # Apply UI/context for new stage
            try:
                self._apply_stage_context()
                # Enable Previous after first stage
                try:
                    self.controls.live_testing_panel.btn_prev.setEnabled(True)
                except Exception:
                    pass
            except Exception:
                pass
        else:
            # Last stage finished — submit results then end session
            self._submit_results_and_end()

    def _on_prev_stage(self) -> None:
        if self._live_session is None:
            return
        if self._live_stage_idx <= 0:
            try:
                self.controls.live_testing_panel.btn_prev.setEnabled(False)
            except Exception:
                pass
            return
        try:
            self._live_stage_idx -= 1
            # Reset active/arming state only (do not touch tare state)
            self._active_cell = None
            self._recent_samples.clear()
            self._arming_cell = None
            self._arming_start_ms = None
            self._apply_stage_context()
            # Disable Previous if at first stage now
            try:
                self.controls.live_testing_panel.btn_prev.setEnabled(self._live_stage_idx > 0)
            except Exception:
                pass
        except Exception:
            pass

    def _apply_stage_context(self) -> None:
        # Update labels, repaint grid colors for current stage, and update Next button/label
        if self._live_session is None:
            return
        try:
            stage = self._live_session.stages[self._live_stage_idx]
        except Exception:
            return
        # Clear and repaint results for this stage
        try:
            self.canvas_left.clear_live_colors()
            self.canvas_right.clear_live_colors()
        except Exception:
            pass
        try:
            # Repaint completed cells
            from PySide6.QtGui import QColor
            model_id = (self._live_session.model_id or "06").strip()
            is_db = (stage.name.lower().find("db") >= 0)
            is_temp = bool(getattr(self._live_session, "is_temp_test", False))
            # Use per-session thresholds (DB fixed-N, BW derived from BW% of body weight)
            base_tol = (
                float(self._live_session.thresholds.dumbbell_tol_n)
                if is_db else float(self._live_session.thresholds.bodyweight_tol_n)
            )
            mult = getattr(config, "COLOR_BIN_MULTIPLIERS", {
                "green": 1.0,
                "light_green": 1.25,
                "yellow": 1.5,
                "orange": 2.0,
                "red": 1e9,
            })
            for (r, c), cell in (stage.results or {}).items():
                try:
                    if cell is None or cell.fz_mean_n is None:
                        continue
                    if is_temp:
                        color = QColor(160, 90, 255, 140)  # purple
                    else:
                        err = cell.error_n
                        if err is None:
                            err = abs(float(cell.fz_mean_n) - float(stage.target_n))
                        if err <= base_tol * mult.get("green", 1.0):
                            color = QColor(0, 200, 0, 120)
                        elif err <= base_tol * mult.get("light_green", 1.25):
                            color = QColor(80, 220, 80, 120)
                        elif err <= base_tol * mult.get("yellow", 1.5):
                            color = QColor(220, 200, 0, 120)
                        elif err <= base_tol * mult.get("orange", 2.0):
                            color = QColor(230, 140, 0, 120)
                        else:
                            color = QColor(220, 0, 0, 120)
                    self.canvas_left.set_live_cell_color(int(r), int(c), color)
                    self.canvas_right.set_live_cell_color(int(r), int(c), color)
                except Exception:
                    continue
        except Exception:
            pass
        # Update progress/labels
        try:
            completed = sum(1 for g in stage.results.values() if g.fz_mean_n is not None)
            total = int(stage.total_cells)
            stage_text = f"Stage {stage.index}: {stage.name} @ {stage.location}"
            self.controls.live_testing_panel.set_stage_progress(stage_text, completed, total)
            # For temperature tests, navigation is automated; keep Next/Prev disabled
            is_temp = bool(getattr(self._live_session, "is_temp_test", False))
            if is_temp:
                # Show a prominent banner on the plate view indicating the current stage
                try:
                    banner = f"{stage.name}"
                    self.canvas_left.set_live_status(banner)
                except Exception:
                    try:
                        self.canvas_left.set_live_status(stage_text)
                    except Exception:
                        pass
                try:
                    self.controls.live_testing_panel.btn_next.setEnabled(False)
                    self.controls.live_testing_panel.btn_prev.setEnabled(False)
                    self.controls.live_testing_panel.set_next_stage_label("Next Stage")
                except Exception:
                    pass
            else:
                # Clear any temp-test banner when not in temp mode
                try:
                    self.canvas_left.set_live_status(None)
                except Exception:
                    pass
                # Enable Next only when stage done in normal mode
                try:
                    self.controls.live_testing_panel.btn_next.setEnabled(bool(completed >= total))
                except Exception:
                    pass
                # Set label to Finish when last stage is next
                if self._live_stage_idx + 1 >= len(self._live_session.stages):
                    self.controls.live_testing_panel.set_next_stage_label("Finish")
                else:
                    self.controls.live_testing_panel.set_next_stage_label("Next Stage")
        except Exception:
            pass

    def _safe_getattr(self, obj: object, name: str, default: object) -> object:
        try:
            return getattr(obj, name, default)  # type: ignore[no-any-return]
        except Exception:
            return default

    def _play_capture_sound(self) -> None:
        """Play short sound after a successful cell capture, if configured."""
        try:
            player = getattr(self, "_capture_sound_player", None)
        except Exception:
            player = None
        if player is None:
            return
        try:
            # Restart from beginning if already playing
            try:
                player.stop()
            except Exception:
                pass
            player.play()
        except Exception:
            pass

    def _on_live_cell_clicked(self, row: int, col: int) -> None:
        # Only in an active session
        if self._live_session is None:
            return
        try:
            stage = self._live_session.stages[self._live_stage_idx]
        except Exception:
            return
        cell = stage.results.get((int(row), int(col)))
        # If cell has not been tested, nothing to show
        if cell is None or cell.fz_mean_n is None:
            return
        # Show a simple dialog with reading and a Retest option
        try:
            msg = QtWidgets.QMessageBox(self)
            msg.setWindowTitle("Cell Result")
            details = f"Row {row}, Col {col}\nMean Fz: {cell.fz_mean_n:.1f} N\nTarget: {stage.target_n:.1f} N\nError: {(cell.error_n or 0.0):.1f} N"
            msg.setText(details)
            retest_btn = msg.addButton("Retest this cell", QtWidgets.QMessageBox.AcceptRole)
            close_btn = msg.addButton("Close", QtWidgets.QMessageBox.RejectRole)
            msg.exec()
            if msg.clickedButton() == retest_btn:
                # Clear the stored value and color; allow re-test
                try:
                    cell.fz_mean_n = None
                    cell.cop_x_mm = None
                    cell.cop_y_mm = None
                    cell.error_n = None
                    cell.color_bin = None
                except Exception:
                    pass
                try:
                    self.canvas_left.clear_live_cell_color(row, col)
                    self.canvas_right.clear_live_cell_color(row, col)
                except Exception:
                    pass
                # Update progress label
                try:
                    completed = sum(1 for g in stage.results.values() if g.fz_mean_n is not None)
                    total = int(stage.total_cells)
                    stage_text = f"Stage {stage.index}: {stage.name} @ {stage.location}"
                    self.controls.live_testing_panel.set_stage_progress(stage_text, completed, total)
                except Exception:
                    pass
        except Exception:
            pass

    def _on_live_end(self) -> None:
        self._log("end_session: cleaning up")
        self._live_session = None
        self._recent_samples.clear()
        self._arming_cell = None
        self._arming_start_ms = None
        self._active_cell = None
        # Reset tare guidance state
        self._tare_active = False
        self._tare_countdown_remaining_s = 0
        self._tare_last_tick_s = None
        self._next_tare_due_ms = None
        # Reset discrete temp flags
        try:
            if hasattr(self, "_discrete_tare_mode"):
                self._discrete_tare_mode = False
            if hasattr(self, "_discrete_ready_for_data"):
                self._discrete_ready_for_data = False
            if hasattr(self, "_discrete_waiting_for_unload"):
                self._discrete_waiting_for_unload = False
            if hasattr(self, "_discrete_pending_tare_complete"):
                self._discrete_pending_tare_complete = False
        except Exception:
            self._discrete_tare_mode = False
            self._discrete_ready_for_data = False
            self._discrete_waiting_for_unload = False
            self._discrete_pending_tare_complete = False
        try:
            if self._tare_dialog is not None:
                self._tare_dialog.reject()
        except Exception:
            pass
        self._tare_dialog = None
        try:
            self.controls.live_testing_panel.btn_start.setEnabled(True)
            self.controls.live_testing_panel.btn_end.setEnabled(False)
            try:
                self.controls.live_testing_panel.btn_next.setEnabled(False)
                self.controls.live_testing_panel.btn_prev.setEnabled(False)
            except Exception:
                pass
            self.controls.live_testing_panel.set_next_stage_label("Next Stage")
            self.controls.live_testing_panel.set_stage_progress("—", 0, 0)
            self.canvas_left.hide_live_grid()
            self.canvas_right.hide_live_grid()
            try:
                self.controls.live_testing_panel.set_current_model("—")
                self.controls.live_testing_panel.set_session_model_id("—")
                self._active_model_label = None
            except Exception:
                pass
        except Exception:
            pass

    def _on_live_tab_selected(self) -> None:
        # On tab switch, fetch model metadata for currently selected device in single mode
        try:
            if self.state.display_mode == "single":
                dev_id = (self.state.selected_device_id or "").strip()
                if dev_id and self._request_model_metadata_cb:
                    self.controls.live_testing_panel.set_current_model("Loading…")
                    self._request_model_metadata_cb(dev_id)
            # Refresh calibration gating on tab select
            self._update_calibration_enabled()
        except Exception:
            pass

    def _compute_summary(self) -> Optional[tuple[str, str, str, str, int, int, str]]:
        # Returns (tester, device_id, model_id_for_results, date_text, pass_cells, total_cells, grade_text)
        if self._live_session is None:
            return None
        try:
            tester = self._live_session.tester_name
            device_id = self._live_session.device_id
            # Prefer active model label from Model pane if available; fall back to session model_id
            model_id_for_results = (self._active_model_label or self._live_session.model_id or "06").strip()
            import datetime
            date_text = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            total_cells = 0
            pass_cells = 0
            # Threshold computation still uses device type/session model for correct per-model tolerances
            tolerance_model_id = (self._live_session.model_id or "06").strip()
            for st in self._live_session.stages:
                is_db = (st.name.lower().find("db") >= 0)
                base_tol = (
                    float(self._live_session.thresholds.dumbbell_tol_n)
                    if is_db else float(self._live_session.thresholds.bodyweight_tol_n)
                )
                for res in st.results.values():
                    if res.error_n is None:
                        continue
                    total_cells += 1
                    if abs(res.error_n) <= base_tol:
                        pass_cells += 1
            ratio = (pass_cells / max(1, total_cells))
            pass_fail = "Pass" if (ratio >= 0.895) else "Fail"
            pct = ratio * 100.0
            def letter(p: float) -> str:
                if p >= 97: return "A+"
                if p >= 93: return "A"
                if p >= 90: return "A-"
                if p >= 87: return "B+"
                if p >= 83: return "B"
                if p >= 80: return "B-"
                if p >= 77: return "C+"
                if p >= 73: return "C"
                if p >= 70: return "C-"
                if p >= 67: return "D+"
                if p >= 63: return "D"
                if p >= 60: return "D-"
                return "F"
            grade_text = letter(pct)
            return (tester, device_id, model_id_for_results, date_text, pass_cells, total_cells, grade_text)
        except Exception:
            return None

    def _submit_results_and_end(self) -> None:
        # If capture active, stop it before computing summary
        try:
            if self._capture_active and (self._capture_group_id or "").strip():
                self.controls.stop_capture_requested.emit({"group_id": self._capture_group_id})
                self._log(f"capture_stopped: group_id='{self._capture_group_id}'")
        except Exception:
            pass
        finally:
            self._capture_active = False
            self._capture_group_id = ""
        # Attempt to rewrite device_id column in capture CSV to real device id
        try:
            self._rewrite_capture_csv_device_id()
        except Exception:
            pass
        # Revert model bypass if we enabled it for this session
        try:
            if self._should_revert_bypass and callable(self._on_set_model_bypass_cb):
                self._on_set_model_bypass_cb(False)
                self._log("model_bypass_reverted:false")
        except Exception:
            pass
        finally:
            self._should_revert_bypass = False

        # Discrete temp sessions: skip full summary dialog/export; just show simple status
        try:
            if self._live_session is not None and bool(getattr(self._live_session, "is_discrete_temp", False)):
                ok = bool(getattr(self, "_discrete_session_success", False))
                text = "Discrete temp session saved to CSV." if ok else "Discrete temp session completed, but no averaged data was written."
                try:
                    QtWidgets.QMessageBox.information(self, "Discrete Temp Testing", text)
                except Exception:
                    pass
                self._on_live_end()
                return
        except Exception:
            # Fall through to normal summary path if anything goes wrong
            pass

        # Temperature tests: skip summary/export dialog; capture/cleanup above is sufficient
        try:
            if self._live_session is not None and bool(getattr(self._live_session, "is_temp_test", False)) and not bool(
                getattr(self._live_session, "is_discrete_temp", False)
            ):
                self._on_live_end()
                return
        except Exception:
            pass

        summary = self._compute_summary()
        if summary is None:
            self._on_live_end()
            return
        try:
            tester, device_id, model_id_for_results, date_text, pass_cells, total_cells, grade_text = summary
            # Determine Pass/Fail: require each stage to meet >=90% passing cells, with 8/9 allowed for 9-cell stages
            plate_passes = True
            try:
                if self._live_session is not None:
                    tolerance_model_id = (self._live_session.model_id or "06").strip()
                    for st in self._live_session.stages:
                        is_db = (st.name.lower().find("db") >= 0)
                        base_tol = (
                            float(self._live_session.thresholds.dumbbell_tol_n)
                            if is_db else float(self._live_session.thresholds.bodyweight_tol_n)
                        )
                        stage_total_cells = int(st.total_cells)
                        stage_pass_cells = 0
                        for res in st.results.values():
                            if res.error_n is None:
                                continue
                            if abs(res.error_n) <= base_tol:
                                stage_pass_cells += 1
                        # Required passing cells per stage
                        if stage_total_cells == 9:
                            required_pass = 8
                        else:
                            import math
                            required_pass = int(math.ceil(0.9 * max(0, stage_total_cells)))
                        if stage_pass_cells < required_pass:
                            plate_passes = False
                            break
            except Exception:
                plate_passes = False
            pass_fail = "Pass" if plate_passes else "Fail"
            dlg = LiveTestSummaryDialog(self)
            dlg.set_values(tester, device_id, model_id_for_results, date_text, pass_fail, pass_cells, total_cells, grade_text)
            if dlg.exec() == LiveTestSummaryDialog.Accepted:
                edited_tester, _ = dlg.get_values()
                try:
                    if getattr(config, "CSV_EXPORT_ENABLED", True):
                        from ..csv_export import append_summary_row
                        bw_n = 0.0
                        try:
                            if self._live_session is not None:
                                bw_n = float(self._live_session.body_weight_n)
                        except Exception:
                            bw_n = 0.0
                        # Prompt for output file (CSV/XLSX) and remember last choice
                        selected_path = ""
                        try:
                            options = QtWidgets.QFileDialog.Options()
                            start_path = self._get_last_export_file() or self._get_last_csv_dir() or QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.DocumentsLocation)
                            filters = "CSV Files (*.csv);;Excel Workbook (*.xlsx);;All Files (*)"
                            selected_path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Choose Results File (CSV/XLSX)", start_path, filters, options=options)
                        except Exception:
                            selected_path = ""
                        if selected_path:
                            try:
                                self._set_last_export_file(selected_path)
                                import os as _os
                                self._set_last_csv_dir(_os.path.dirname(selected_path))
                            except Exception:
                                pass
                        csv_path = append_summary_row(device_id, pass_fail, date_text, edited_tester, bw_n, model_id_for_results, path=(selected_path or None))
                        try:
                            QtWidgets.QMessageBox.information(self, "Export Complete", f"Summary saved to CSV:\n{csv_path}")
                        except Exception:
                            pass
                    else:
                        from ..ms_graph_excel import append_summary_row
                        bw_n = 0.0
                        try:
                            if self._live_session is not None:
                                bw_n = float(self._live_session.body_weight_n)
                        except Exception:
                            bw_n = 0.0
                        append_summary_row(device_id, pass_fail, date_text, edited_tester, bw_n, model_id_for_results)
                except Exception as e:
                    try:
                        QtWidgets.QMessageBox.warning(self, "Export Failed", f"Could not save summary: {e}")
                    except Exception:
                        pass
        except Exception:
            pass
        self._on_live_end()

    # Controller callback registrations
    def on_update_dynamo_config(self, slot: Callable[[str, object], None]) -> None:
        self._on_update_dynamo_config_cb = slot

    def on_set_model_bypass(self, slot: Callable[[bool], None]) -> None:
        self._on_set_model_bypass_cb = slot

    def on_resolve_group_id(self, slot: Callable[[str], Optional[str]]) -> None:
        self._resolve_group_id_cb = slot

    # --- Capture CSV post-processing ---
    def _rewrite_capture_csv_device_id(self) -> None:
        try:
            import os
            import csv
            directory = str(getattr(self, "_capture_csv_dir", "") or "")
            name = str(getattr(self, "_capture_csv_name", "") or "")
            real_id = str(getattr(self, "_capture_device_id_real", "") or "")
            if not directory or not name or not real_id:
                return
            path = os.path.join(directory, f"{name}.csv")
            if not os.path.isfile(path):
                return
            # Read and rewrite only if 'device_id' column exists
            tmp_path = path + ".tmp"
            with open(path, "r", newline="", encoding="utf-8") as fin, open(tmp_path, "w", newline="", encoding="utf-8") as fout:
                reader = csv.reader(fin)
                writer = csv.writer(fout)
                header = next(reader, None)
                if not header:
                    return
                writer.writerow(header)
                # Try common column names
                idx = -1
                candidates = ["device_id", "deviceId", "device"]
                low = [h.strip() for h in header]
                for cand in candidates:
                    try:
                        if cand in low:
                            idx = low.index(cand)
                            break
                    except Exception:
                        continue
                if idx < 0:
                    # Write remainder unchanged
                    for row in reader:
                        writer.writerow(row)
                else:
                    for row in reader:
                        try:
                            if len(row) > idx:
                                row[idx] = real_id
                        except Exception:
                            pass
                        writer.writerow(row)
            try:
                os.replace(tmp_path, path)
            except Exception:
                try:
                    # Fallback on Windows
                    os.remove(path)
                    os.rename(tmp_path, path)
                except Exception:
                    pass
        except Exception:
            pass

    # Called by controller when stopCaptureStatus returns success
    def on_capture_stopped(self, _payload: Optional[dict] = None) -> None:
        try:
            self._rewrite_capture_csv_device_id()
        except Exception:
            pass

    # --- Temperature Testing handlers ---
    def _scan_temp_devices(self) -> List[str]:
        """Scan local and OneDrive temp_testing folders for device directories."""
        devices = set()
        
        # 1. Scan local repo temp_testing
        try:
            local_root = os.path.join(self._repo_root(), "temp_testing")
            if os.path.isdir(local_root):
                with os.scandir(local_root) as it:
                    for entry in it:
                        if entry.is_dir() and not entry.name.startswith("."):
                            devices.add(entry.name)
        except Exception:
            pass

        # 2. Scan OneDrive temp_testing
        try:
            onedrive_root = data_sync.get_onedrive_data_root()
            if onedrive_root and os.path.isdir(onedrive_root):
                remote_root = os.path.join(onedrive_root, "temp_testing")
                if os.path.isdir(remote_root):
                    with os.scandir(remote_root) as it:
                        for entry in it:
                            if entry.is_dir() and not entry.name.startswith("."):
                                devices.add(entry.name)
        except Exception:
            pass
            
        return sorted(list(devices))

    def _resolve_temp_device_path(self, device_id: str) -> str:
        """Find the path for a device, preferring local, then OneDrive."""
        if not device_id:
            return ""
            
        # Check local first
        local_path = os.path.join(self._repo_root(), "temp_testing", device_id)
        if os.path.isdir(local_path):
            return local_path
            
        # Removed OneDrive check from default resolution
        # onedrive_root = data_sync.get_onedrive_data_root()
        # if onedrive_root:
        #     remote_path = os.path.join(onedrive_root, "temp_testing", device_id)
        #     if os.path.isdir(remote_path):
        #         return remote_path
                
        return ""

    def _on_temp_refresh_devices(self) -> None:
        self.controls.temperature_testing_panel.btn_refresh.setText("Syncing...")
        self.controls.temperature_testing_panel.btn_refresh.setEnabled(False)
        self.controls.temperature_testing_panel.btn_refresh.repaint()
        QtWidgets.QApplication.processEvents()
        
        # 1. Sync from OneDrive if available
        try:
            import shutil
            
            onedrive_root = data_sync.get_onedrive_data_root()
            if onedrive_root:
                remote_root = os.path.join(onedrive_root, "temp_testing")
                local_root = os.path.join(self._repo_root(), "temp_testing")
                
                if os.path.isdir(remote_root):
                    # Iterate over remote devices
                    for entry in os.scandir(remote_root):
                        if entry.is_dir() and not entry.name.startswith("."):
                            device_id = entry.name
                            src_dir = entry.path
                            dst_dir = os.path.join(local_root, device_id)
                            
                            # Case 1: Device folder doesn't exist locally -> Copy entire folder
                            if not os.path.isdir(dst_dir):
                                try:
                                    shutil.copytree(src_dir, dst_dir)
                                    self._log(f"synced new device folder: {device_id}")
                                except Exception:
                                    pass
                            else:
                                # Case 2: Device folder exists -> Copy missing files
                                try:
                                    for root, _, files in os.walk(src_dir):
                                        # Relative path from device folder
                                        rel_path = os.path.relpath(root, src_dir)
                                        dst_subdir = os.path.join(dst_dir, rel_path)
                                        
                                        if not os.path.isdir(dst_subdir):
                                            os.makedirs(dst_subdir, exist_ok=True)
                                            
                                        for f in files:
                                            if f.startswith("."): continue
                                            src_file = os.path.join(root, f)
                                            dst_file = os.path.join(dst_subdir, f)
                                            
                                            if not os.path.exists(dst_file):
                                                shutil.copy2(src_file, dst_file)
                                                # self._log(f"synced file: {f}")
                                except Exception:
                                    pass
        except Exception as e:
            self._log(f"sync error: {e}")
            pass
        finally:
            self.controls.temperature_testing_panel.btn_refresh.setText("Refresh")
            self.controls.temperature_testing_panel.btn_refresh.setEnabled(True)

        # 2. Refresh list from local only
        devices = self._scan_temp_devices()
        self.controls.temperature_testing_panel.set_devices(devices)
        # If devices exist, select the first one (or keep current if valid)
        current = self.controls.temperature_testing_panel.device_combo.currentText()
        if current in devices:
            self.controls.temperature_testing_panel.device_combo.setCurrentText(current)
        elif devices:
            self.controls.temperature_testing_panel.device_combo.setCurrentIndex(0)

    def _on_temp_device_selected(self, device_id: str) -> None:
        device_id = device_id.strip()
        if not device_id:
            self.controls.temperature_testing_panel.set_tests([])
            self.controls.temperature_testing_panel.set_device_id("—")
            self.controls.temperature_testing_panel.set_model_label("—")
            self.controls.temperature_testing_panel.set_body_weight_n(None)
            return

        self.controls.temperature_testing_panel.set_device_id(device_id)
        
        # Collect tests from local ONLY
        unique_tests = {}  # filename -> full_path
        
        # 1. Local
        local_path = os.path.join(self._repo_root(), "temp_testing", device_id)
        if os.path.isdir(local_path):
            for f in self._collect_csvs(local_path):
                unique_tests[os.path.basename(f)] = f
                
        # Removed OneDrive check
        # onedrive_root = data_sync.get_onedrive_data_root()
        # ...

        if not unique_tests:
            self.controls.temperature_testing_panel.set_tests([])
            return

        # Populate tests with formatted names
        test_items = []
        import json
        
        for fname in sorted(unique_tests.keys()):
            f = unique_tests[fname]
            try:
                # Check for .meta.json
                meta_path = f.replace(".csv", ".meta.json")
                display_name = fname
                
                if os.path.isfile(meta_path):
                    try:
                        with open(meta_path, "r", encoding="utf-8") as mf:
                            meta = json.load(mf)
                            
                        tester = meta.get("tester_name", "Unknown")
                        date_str = meta.get("date", "Unknown")
                        
                        # Check cached temp
                        avg_temp = meta.get("avg_temp")
                        if avg_temp is None:
                            # Calculate and cache
                            avg_temp = self._calculate_avg_temp(f)
                            meta["avg_temp"] = avg_temp
                            # Try to write back
                            try:
                                with open(meta_path, "w", encoding="utf-8") as mf:
                                    json.dump(meta, mf, indent=2)
                            except Exception:
                                pass
                        
                        display_name = f"(raw) {tester}_{date_str}_{float(avg_temp):.1f}F"
                    except Exception:
                        pass
                
                test_items.append((display_name, f))
            except Exception:
                pass
                
        # Update panel with (label, path) tuples
        self.controls.temperature_testing_panel.set_tests_with_labels(test_items)
        
        # Load last known body weight
        try:
            bw_last = meta_store.get_latest_body_weight(device_id)
        except Exception:
            bw_last = None
        self.controls.temperature_testing_panel.set_body_weight_n(bw_last)
        
        # Request metadata
        if self._request_model_metadata_cb:
            self._temp_meta_device_id = device_id
            try:
                self._request_model_metadata_cb(device_id)
            except Exception:
                pass

    def _calculate_avg_temp(self, csv_path: str) -> float:
        """Calculate average temperature from 'sum-t' column, sampling every 1000 rows."""
        try:
            import csv
            total_temp = 0.0
            count = 0
            with open(csv_path, "r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for i, row in enumerate(reader):
                    if i % 1000 == 0:
                        try:
                            # 'sum-t' or 'sum_t' or 'temp'
                            val = row.get("sum-t") or row.get("sum_t") or row.get("temp")
                            if val:
                                total_temp += float(val)
                                count += 1
                        except ValueError:
                            pass
            return (total_temp / count) if count > 0 else 0.0
        except Exception:
            return 0.0

    def _on_temp_run_requested(self, payload: dict) -> None:
        # Expect payload: { device_id, csv_path, slopes:{x,y,z} }
        try:
            device_id = str(payload.get("device_id") or "").strip()
            csv_path = str(payload.get("csv_path") or "").strip()
            slopes = payload.get("slopes") or {"x": 3.0, "y": 3.0, "z": 3.0}
            
            # Resolve folder from device_id
            folder = self._resolve_temp_device_path(device_id)
        except Exception:
            return
            
        if not (folder and device_id and csv_path):
            try:
                QtWidgets.QMessageBox.information(self, "Temperature Testing", "Please select a device and a CSV test file.")
            except Exception:
                pass
            return
        
        # Ensure view state is configured for single-device discrete testing so heatmaps render
        try:
            self.state.display_mode = "single"
            self.state.selected_device_id = device_id
            # Infer device type from ID (e.g. "07.xxxx" -> "07")
            if device_id:
                parts = device_id.split(".")
                if len(parts) > 1:
                    self.state.selected_device_type = parts[0]
                elif "-" in device_id:
                    self.state.selected_device_type = device_id.split("-")[0]
        except Exception:
            pass

        try:
            self._log(f"temp_run: folder='{folder}' device='{device_id}' csv='{csv_path}' slopes={slopes}")
        except Exception:
            pass
        # Delegate to controller to perform backend config and processing twice
        on_path = None
        off_path = None
        if hasattr(self, "_on_temp_process_cb") and callable(self._on_temp_process_cb):
            try:
                self._log("temp_run: invoking controller.run_temperature_processing")
                res = self._on_temp_process_cb(folder, device_id, csv_path, dict(slopes))
                if isinstance(res, tuple) and len(res) == 2:
                    on_path, off_path = res
                self._log(f"temp_run: controller returned on='{on_path}' off='{off_path}'")
            except Exception:
                on_path, off_path = None, None
                try:
                    self._log("temp_run: controller call raised exception")
                except Exception:
                    pass
        # Ingest processed CSVs into heatmap view and update processed list
        try:
            if off_path:
                self._log(f"temp_run: ingest OFF path='{off_path}'")
                self._ingest_processed_csv("TEMP OFF", off_path)
                self._temp_baseline_key = off_path
            if on_path:
                self._log(f"temp_run: ingest ON path='{on_path}'")
                self._ingest_processed_csv("TEMP ON", on_path)
                self._temp_selected_key = on_path
            # Rebuild processed runs list for this csv
            self._log("temp_run: rebuilding processed runs list")
            self._on_temp_test_changed(csv_path)
            # Apply views
            self._log("temp_run: applying views")
            self._apply_temp_views()
            self._log("temp_run: apply views done")
        except Exception:
            pass

    def _find_best_windows(self, times: list[float], forces: list[float], targets: dict[str, float]) -> dict[str, dict]:
        """
        Find best 2s windows for each target load.
        Returns dict: { '45': {'val': float, 'err': float}, 'bw': ... }
        """
        results = {}
        if not times or len(times) < 10:
            return results
            
        # Parameters
        WINDOW_DURATION = 2000  # ms
        TOLERANCE_PCT = 0.30
        STD_THRESHOLD = 5.0
        
        # Pre-calculate valid windows
        # This is O(N*W), potentially slow if not careful. 
        # 20k points, window ~120 points (60Hz * 2s). 
        # Sliding window calculation is better.
        
        import math
        import statistics
        
        n = len(times)
        
        # We can optimize by iterating start indices
        # Assume roughly constant sampling rate
        t_start = times[0]
        t_end = times[-1]
        if t_end - t_start < WINDOW_DURATION:
            return results
            
        # We need to iterate through windows.
        # Optimization: Step by e.g. 10 samples (approx 1/6th sec)
        step = 5
        
        best_candidates = {k: {'slope': float('inf'), 'std': float('inf'), 'val': 0.0} for k in targets}
        found_any = {k: False for k in targets}
        
        i = 0
        while i < n:
            t0 = times[i]
            # Find end index
            j = i
            while j < n and times[j] - t0 < WINDOW_DURATION:
                j += 1
            
            if j >= n: 
                break
                
            # Window is slice [i:j]
            window_forces = forces[i:j]
            window_times = times[i:j]
            
            if len(window_forces) < 10:
                i += step
                continue
                
            mean_f = statistics.mean(window_forces)
            
            # Check if this window matches any target
            for tag, target_val in targets.items():
                if target_val <= 0: continue
                
                diff = abs(mean_f - target_val)
                if diff / target_val <= TOLERANCE_PCT:
                    # Candidate
                    try:
                        std_f = statistics.stdev(window_forces)
                    except Exception:
                        std_f = 0.0
                    
                    # Calculate slope (linear regression)
                    # slope = cov(t, f) / var(t)
                    # t in seconds for meaningful slope (N/s)
                    wt_s = [(t - t0)/1000.0 for t in window_times]
                    mean_t = statistics.mean(wt_s)
                    
                    num = sum((t - mean_t) * (f - mean_f) for t, f in zip(wt_s, window_forces))
                    denom = sum((t - mean_t)**2 for t in wt_s)
                    slope = (num / denom) if denom > 0 else float('inf')
                    
                    # Selection criteria:
                    # "slope closest to 0 and std smallest"
                    # We prioritize std < 5.0. If both < 5.0, pick min slope.
                    # If neither < 5.0, pick min std.
                    
                    curr_best = best_candidates[tag]
                    is_better = False
                    
                    if not found_any[tag]:
                        is_better = True
                    else:
                        # Logic:
                        # If new is low noise and old is high noise -> new
                        # If both low noise -> min slope
                        # If both high noise -> min std (or min slope? User said "slope closest to 0 and std is smallest")
                        
                        curr_low_noise = curr_best['std'] < STD_THRESHOLD
                        new_low_noise = std_f < STD_THRESHOLD
                        
                        if new_low_noise and not curr_low_noise:
                            is_better = True
                        elif new_low_noise and curr_low_noise:
                            if abs(slope) < abs(curr_best['slope']):
                                is_better = True
                        elif not new_low_noise and not curr_low_noise:
                            # Both noisy: minimize combined score? Or just slope?
                            # "std is the smallest" implies minimizing std is important.
                            # Let's minimize std primarily if it's high
                            if std_f < curr_best['std']:
                                is_better = True
                                
                    if is_better:
                        best_candidates[tag] = {'slope': slope, 'std': std_f, 'val': mean_f}
                        found_any[tag] = True
            
            i += step
            
        for tag, found in found_any.items():
            if found:
                val = best_candidates[tag]['val']
                target = targets[tag]
                err = (val - target) / target if target > 0 else 0.0
                results[tag] = {'val': val, 'err': err, 'target': target}
                
        return results

    def _ingest_processed_csv(self, tag: str, csv_path: str, t_start_ms: Optional[int] = None, t_end_ms: Optional[int] = None) -> None:
        # New implementation: Smart Window Finding
        import os
        import csv as _csv
        import statistics
        
        # Storage for this run
        # We use a class attribute dict to store results by (tag, csv_path)
        if not hasattr(self, "_temp_grid_results"):
            self._temp_grid_results = {}
            
        # Clear previous for this specific key if needed, or just overwrite
        # We key by 'tag' (TEMP ON / TEMP OFF)
        # Actually, better to store by tag so we can retrieve easily in _apply_temp_views
        
        device_id = self.state.selected_device_id
        # Determine grid
        grid_dims = GRID_BY_MODEL.get(self.state.selected_device_type, (3, 3))
        rows, cols = grid_dims
        
        # Targets
        try:
            bw_text = self.controls.temperature_testing_panel.lbl_bw.text()
            bw_val = float(bw_text) if bw_text and bw_text != "—" else 0.0
        except Exception:
            bw_val = 0.0
            
        # Fallback to meta_store if UI empty
        if bw_val <= 0:
             try:
                 bw_val = meta_store.get_latest_body_weight(device_id) or 0.0
             except Exception:
                 pass
                 
        targets = {
            "45 lb": 200.0,  # Approx 45 lbs in N
            "Body Weight": bw_val
        }
        
        # Bin data by cell
        cell_data = {} # (r, c) -> {'times': [], 'forces': []}
        
        try:
            self._log(f"ingest_csv_smart: tag='{tag}' path='{csv_path}' bw={bw_val}")
            with open(csv_path, "r", newline="", encoding="utf-8") as f:
                # Robust header reading
                header_line = f.readline()
                if not header_line: return
                import io
                headers = [h.strip() for h in next(_csv.reader(io.StringIO(header_line)), [])]
                reader = _csv.DictReader(f, fieldnames=headers)
                
                for row in reader:
                    try:
                        t_ms = float(row.get("time", row.get("time_ms", 0)) or 0)
                        # Time filter
                        if t_start_ms is not None and t_ms < t_start_ms: continue
                        if t_end_ms is not None and t_ms > t_end_ms: continue
                        
                        # Position
                        try:
                            x_mm = float(row.get("COPx", row.get("x_mm", row.get("x", 0.0))) or 0.0)
                            y_mm = float(row.get("COPy", row.get("y_mm", row.get("y", 0.0))) or 0.0)
                        except Exception:
                            x_mm = 0.0; y_mm = 0.0
                            
                        # Force
                        try:
                            sum_z = float(row.get("sum-z", row.get("sum_z", 0.0)) or 0.0)
                        except Exception:
                            sum_z = 0.0
                            
                        if abs(sum_z) < 5.0: continue # Ignore empty frames
                        
                        # Map to cell
                        # We need plate dimensions
                        # Use self._live_session helpers if available, or re-implement
                        # Re-implement to be safe/independent
                        
                        # Plate dims
                        if self.state.selected_device_type == "08":
                            w_mm, h_mm = config.TYPE08_W_MM, config.TYPE08_H_MM
                        elif self.state.selected_device_type == "07":
                            w_mm, h_mm = config.TYPE07_W_MM, config.TYPE07_H_MM
                        elif self.state.selected_device_type == "06":
                            w_mm, h_mm = config.TYPE06_W_MM, config.TYPE06_H_MM
                        elif self.state.selected_device_type == "11":
                            w_mm, h_mm = config.TYPE11_W_MM, config.TYPE11_H_MM
                        else:
                            w_mm, h_mm = config.TYPE06_W_MM, config.TYPE06_H_MM
                            
                        # Map from center-origin to top-left
                        # x is right (+), y is down (+)?
                        # In WorldCanvas/config, Origin Y fraction is used.
                        # Assuming standard cartesian: x right, y up.
                        # Config: TYPE06_W_MM is width (x). TYPE06_H_MM is height (y).
                        # 0,0 is center.
                        # x_idx = (x + w/2) / (w/cols)
                        # y_idx = (h/2 - y) / (h/rows)  <-- Assuming y up is positive
                        # Let's check row_col_from_xy implementation in this file
                        # It's not readily available as a simple function.
                        
                        # Let's assume standard: x+ right, y+ up.
                        # Columns: x from -W/2 to W/2
                        c = int( (x_mm + w_mm/2.0) / (w_mm/cols) )
                        # Rows: y from H/2 to -H/2 (top to bottom)
                        r = int( (h_mm/2.0 - y_mm) / (h_mm/rows) )
                        
                        c = max(0, min(cols-1, c))
                        r = max(0, min(rows-1, r))
                        
                        k = (r, c)
                        if k not in cell_data:
                            cell_data[k] = {'times': [], 'forces': []}
                        cell_data[k]['times'].append(t_ms)
                        cell_data[k]['forces'].append(sum_z)
                        
                    except Exception:
                        continue
                        
        except Exception as e:
            self._log(f"ingest error: {e}")
            return
            
        # Analyze each cell
        grid_results = {}
        for k, data in cell_data.items():
            try:
                res = self._find_best_windows(data['times'], data['forces'], targets)
                if res:
                    grid_results[k] = res
            except Exception:
                continue
                
        # Store results
        self._temp_grid_results[tag] = grid_results
        self._log(f"ingest complete: {len(grid_results)} active cells")
        
        # Update processed list if needed (handled by caller)

    def _apply_temp_views(self) -> None:
        # Apply heatmaps/grids based on current selection and results
        try:
            target_mode = self.controls.temperature_testing_panel.target_combo.currentText() # "45 lb", "Body Weight", "Both"
            
            # Left: Baseline (OFF)
            # Right: Selected (ON)
            
            for tag, canvas in [("TEMP OFF", self.canvas_left), ("TEMP ON", self.canvas_right)]:
                data = self._temp_grid_results.get(tag, {})
                
                # Reset overlay
                canvas.overlay.clear_colors()
                
                # We need to determine rows/cols again to set grid
                # Or just set it once when device selected.
                # Safer to set it here.
                grid_dims = GRID_BY_MODEL.get(self.state.selected_device_type, (3, 3))
                canvas.overlay.set_grid(*grid_dims)
                
                for (r, c), res in data.items():
                    # Determine value/color based on target_mode
                    
                    errs = []
                    vals = []
                    
                    if target_mode == "45 lb" or target_mode == "Both":
                        if "45 lb" in res:
                            errs.append(res["45 lb"]["err"])
                            vals.append(res["45 lb"]["val"])
                            
                    if target_mode == "Body Weight" or target_mode == "Both":
                        if "Body Weight" in res:
                            errs.append(res["Body Weight"]["err"])
                            vals.append(res["Body Weight"]["val"])
                            
                    if not errs:
                        continue
                        
                    # Use average abs error for color?
                    # Or max error?
                    # "display numbers on each cell showing percent off"
                    
                    avg_err = sum(errs) / len(errs)
                    avg_abs_err = sum(abs(e) for e in errs) / len(errs)
                    
                    # Color logic
                    # COLOR_BIN_MULTIPLIERS logic (from config)
                    # thresholds are model specific
                    # But here we use percentage error directly? 
                    # Config uses thresholds N or Pct.
                    # "Color grid cells based on % error from target (using existing live testing thresholds)."
                    
                    # existing thresholds: THRESHOLDS_BW_PCT_BY_MODEL
                    thresh_pct = config.THRESHOLDS_BW_PCT_BY_MODEL.get(self.state.selected_device_type, 0.01)
                    
                    ratio = avg_abs_err / thresh_pct if thresh_pct > 0 else 0.0
                    
                    # Bin color
                    mult = getattr(config, "COLOR_BIN_MULTIPLIERS", {"green": 1.0, "light_green": 1.25, "yellow": 1.5, "orange": 2.0})
                    if ratio <= mult.get("green", 1.0):
                        color_name = "green"
                        qcolor = QtGui.QColor(40, 180, 40, 160)
                    elif ratio <= mult.get("light_green", 1.25):
                        color_name = "light_green"
                        qcolor = QtGui.QColor(120, 220, 60, 160)
                    elif ratio <= mult.get("yellow", 1.5):
                        color_name = "yellow"
                        qcolor = QtGui.QColor(220, 220, 40, 160)
                    elif ratio <= mult.get("orange", 2.0):
                        color_name = "orange"
                        qcolor = QtGui.QColor(255, 140, 20, 160)
                    else:
                        color_name = "red"
                        qcolor = QtGui.QColor(220, 40, 40, 160)
                        
                    canvas.overlay.set_cell_color(r, c, qcolor)
                    canvas.overlay.set_cell_text(r, c, f"{avg_err*100:.1f}%")
                    
                canvas.update()
                
        except Exception as e:
            self._log(f"apply views error: {e}")
            pass

    def _collect_csvs(self, directory: str) -> list[str]:
        # Return CSV files in the directory (non-recursive only)
        import os
        results: list[str] = []
        try:
            for name in os.listdir(directory):
                try:
                    # Be robust to multiple dots; check last extension only
                    stripped = name.strip()
                    last_dot = stripped.rfind(".")
                    ext = stripped[last_dot + 1 :].lower() if last_dot >= 0 else ""
                    if ext == "csv":
                        results.append(os.path.join(directory, stripped))
                except Exception:
                    continue
        except Exception:
            results = []
        return results

    # --- Temperature Testing stage handlers ---
    def _on_temp_test_changed(self, csv_path: str) -> None:
        # Build processed runs list and stage list for this CSV
        entries: list[dict] = []
        stages_ui: list[str] = ["All"]
        try:
            # Processed runs
            runs = meta_store.get_runs_for_csv(csv_path)
            try:
                self._log(f"temp_runs: {len(runs)} entries for csv='{csv_path}'")
            except Exception:
                pass
            baseline_added = False
            for r in runs:
                # Filter out baseline-only entries from the UI list (per request)
                # We still need baseline paths logic internally, but user only picks "slopes"
                # offp = (r or {}).get("output_off")
                onp = (r or {}).get("output_on")
                # if offp and not baseline_added:
                #     entries.append({"label": "No values (Baseline)", "path": offp, "is_baseline": True})
                #     baseline_added = True
                if onp:
                    sx = r.get("slope_x"); sy = r.get("slope_y"); sz = r.get("slope_z")
                    label = f"{sx:.3g}, {sy:.3g}, {sz:.3g}" if all(v is not None for v in (sx, sy, sz)) else "x,y,z"
                    entries.append({"label": label, "path": onp, "slope_x": sx, "slope_y": sy, "slope_z": sz, "is_baseline": False})
            # Stage list (from DB)
            try:
                device_id = self.controls.temperature_testing_panel.lbl_device_id.text().strip()
                import os as _os
                cap_name = _os.path.splitext(_os.path.basename(csv_path))[0]
                marks = meta_store.get_stage_marks(device_id, cap_name)
                names = [m.get("stage_name") for m in (marks or []) if m.get("stage_name")]
                # unique order-preserving
                seen = set()
                for n in names:
                    if n not in seen:
                        stages_ui.append(n)
                        seen.add(n)
                try:
                    self._log(f"temp_stages: {len(stages_ui)-1} stages for capture='{cap_name}'")
                except Exception:
                    pass
            except Exception:
                pass
        except Exception:
            entries = []
        try:
            self.controls.temperature_testing_panel.set_processed_runs(entries)
            self.controls.temperature_testing_panel.set_stages(stages_ui)
        except Exception:
            pass
        # Set baseline key if available
        try:
            base = next((e for e in entries if e.get("is_baseline")), None)
            if base:
                self._temp_baseline_raw = str(base.get("path"))
        except Exception:
            pass

    def _on_temp_processed_selected(self, entry: dict) -> None:
        path = str((entry or {}).get("path") or "")
        if not path:
            return
        self._temp_selected_raw = path
        
        # Ensure view state is single mode (robustness)
        try:
            self.state.display_mode = "single"
            did = self.controls.temperature_testing_panel.lbl_device_id.text().strip()
            if did and did != "—":
                self.state.selected_device_id = did
                if "." in did:
                    self.state.selected_device_type = did.split(".")[0]
                elif "-" in did:
                    self.state.selected_device_type = did.split("-")[0]
        except Exception:
            pass

        # Re-apply views to reflect new selection
        self._apply_temp_views()

    def _on_temp_view_mode_changed(self, mode: str) -> None:
        self._temp_view_mode = mode or "Heatmap"
        self._apply_temp_views()

    def _on_temp_stage_changed(self, stage_name: str) -> None:
        self._temp_stage_name = stage_name or "All"
        self._apply_temp_views()

    def _stage_window_for(self, csv_path: str, stage_name: str) -> tuple[Optional[int], Optional[int]]:
        if not stage_name or stage_name == "All":
            return (None, None)
        try:
            device_id = self.controls.temperature_testing_panel.lbl_device_id.text().strip()
        except Exception:
            device_id = ""
        if not device_id:
            return (None, None)
        import os as _os
        cap_name = _os.path.splitext(_os.path.basename(csv_path))[0]
        marks = meta_store.get_stage_marks(device_id, cap_name)
        # Combine windows of same stage into [min_start, max_end]
        starts = []
        ends = []
        for m in (marks or []):
            if (m or {}).get("stage_name") == stage_name:
                s = m.get("start_ms"); e = m.get("end_ms")
                if s is not None:
                    starts.append(int(s))
                if e is not None:
                    ends.append(int(e))
        if not starts:
            return (None, None)
        t0 = min(starts)
        t1 = max(ends) if ends else None
        return (t0, t1)

    def _apply_temp_views(self) -> None:
        # Determine time window for stage
        t0 = t1 = None
        stage = getattr(self, "_temp_stage_name", "All")
        base_raw = getattr(self, "_temp_baseline_raw", None)
        sel_raw = getattr(self, "_temp_selected_raw", None)
        try:
            self._log(f"apply_views: stage='{stage}' base='{base_raw or ''}' sel='{sel_raw or ''}'")
        except Exception:
            pass
        if stage and stage != "All":
            # Derive stage windows from baseline if possible; else from selected
            ref = base_raw or sel_raw
            if ref:
                t0, t1 = self._stage_window_for(ref, stage)
        try:
            self._log(f"apply_views: time_window=({t0},{t1})")
        except Exception:
            pass
        # If a specific stage is selected, replay processed CSV like live (arming + stability) and color grid by accuracy
        stage_rendered = False
        if stage and stage != "All":
            try:
                dev_type = (self.state.selected_device_type or "06").strip()
                rows, cols = getattr(config, "GRID_DIMS_BY_MODEL", {}).get(dev_type, (3, 3))
                # Prepare canvases
                self.canvas_left.set_heatmap_points([])
                self.canvas_right.set_heatmap_points([])
                self.canvas_left.show_live_grid(int(rows), int(cols))
                self.canvas_right.show_live_grid(int(rows), int(cols))
                # Targets and tolerance for this stage
                def _targets_for(stage_name: str) -> tuple[float, float]:
                    lb_to_n = 4.44822
                    is_db = (stage_name.lower().find("db") >= 0)
                    if is_db:
                        target = 45.0 * lb_to_n
                        tol_n = float(getattr(config, "THRESHOLDS_DB_N_BY_MODEL", {}).get(dev_type, 6.0))
                    else:
                        try:
                            bw_n = meta_store.get_latest_body_weight(self.controls.temperature_testing_panel.lbl_device_id.text().strip()) or 0.0
                        except Exception:
                            bw_n = 0.0
                        pct = float(getattr(config, "THRESHOLDS_BW_PCT_BY_MODEL", {}).get(dev_type, 0.01))
                        target = float(bw_n)
                        tol_n = round(float(bw_n) * pct, 1)
                    return float(target), float(tol_n)
                target_n, tol_n = _targets_for(stage or "Body Weight")
                # Replay helper
                from collections import deque
                def _replay(csv_path: str) -> dict[tuple[int, int], tuple[float, float]]:
                    results: dict[tuple[int, int], tuple[float, float]] = {}
                    if not csv_path:
                        return results
                    try:
                        import csv as _csv
                        with open(csv_path, "r", newline="", encoding="utf-8") as f:
                            # Read header line manually to strip whitespace
                            header_line = f.readline()
                            if not header_line:
                                return results
                            import io
                            header_reader = _csv.reader(io.StringIO(header_line))
                            headers = next(header_reader, [])
                            headers = [h.strip() for h in headers]
                            r = _csv.DictReader(f, fieldnames=headers)

                            active_cell: tuple[int, int] | None = None
                            window: deque[tuple[int, float, float, float]] = deque()
                            arming_cell: tuple[int, int] | None = None
                            arming_start: int | None = None
                            for row in r:
                                try:
                                    t_ms = int(float(row.get("time", row.get("time_ms", 0)) or 0))
                                except Exception:
                                    continue
                                if t0 is not None and t_ms < int(t0):
                                    continue
                                if t1 is not None and t_ms > int(t1):
                                    break
                                try:
                                    x_mm = float(row.get("COPx", 0.0) or 0.0)
                                    y_mm = float(row.get("COPy", 0.0) or 0.0)
                                    fz = float(row.get("sum-z", row.get("sum_z", 0.0)) or 0.0)
                                except Exception:
                                    continue
                                cell = self._cell_from_mm(x_mm, y_mm, dev_type, int(rows), int(cols))
                                if cell is None:
                                    arming_cell = None
                                    arming_start = None
                                    active_cell = None
                                    window.clear()
                                    continue
                                # append and trim window to stability horizon
                                window.append((t_ms, x_mm, y_mm, fz))
                                while window and (window[-1][0] - window[0][0]) > max(0, self._stability_window_ms):
                                    window.popleft()
                                # Arming (≥50 N for 2.0s in same cell)
                                if active_cell is None:
                                    if abs(fz) >= 50.0:
                                        if arming_cell == cell:
                                            arm_span = int(t_ms) - int(arming_start or t_ms)
                                        else:
                                            arming_cell = cell
                                            arming_start = int(t_ms)
                                            arm_span = 0
                                        if (cell not in results) and arm_span >= self._arming_window_ms:
                                            active_cell = cell
                                            window.clear()
                                    else:
                                        arming_cell = None
                                        arming_start = None
                                        continue
                                else:
                                    if cell != active_cell:
                                        active_cell = None
                                        window.clear()
                                        continue
                                    if not window:
                                        continue
                                    t_span = window[-1][0] - window[0][0]
                                    required_ms = max(0, self._stability_window_ms - self._stability_tolerance_ms)
                                    if t_span < required_ms:
                                        continue
                                    vals = [w[3] for w in window]
                                    mean_fz = sum(vals) / len(vals)
                                    var = sum((v - mean_fz) ** 2 for v in vals) / max(1, (len(vals) - 1))
                                    std_fz = var ** 0.5
                                    if std_fz <= 5.0:
                                        err_n = abs(mean_fz - target_n)
                                        results[active_cell] = (float(mean_fz), float(err_n))
                                        active_cell = None
                                        window.clear()
                        return results
                    except Exception:
                        return results
                # Paint results
                from PySide6.QtGui import QColor
                mult = getattr(config, "COLOR_BIN_MULTIPLIERS", {"green": 1.0, "light_green": 1.25, "yellow": 1.5, "orange": 2.0})
                def _paint(results: dict[tuple[int, int], tuple[float, float]], canvas) -> dict:
                    metrics = {"count": 0, "mean_pct": 0.0, "median_pct": 0.0, "max_pct": 0.0, "signed_bias_pct": 0.0}
                    if not results:
                        return metrics
                    pcts = []
                    signed = []
                    for (r, c), (mean_fz, err_n) in results.items():
                        pct = (abs(err_n) / (target_n if abs(target_n) > 1e-6 else 1.0)) * 100.0
                        pcts.append(pct)
                        signed.append(((mean_fz - target_n) / (target_n if abs(target_n) > 1e-6 else 1.0)) * 100.0)
                        if err_n <= tol_n * mult.get("green", 1.0):
                            color = QColor(0, 200, 0, 120)
                        elif err_n <= tol_n * mult.get("light_green", 1.25):
                            color = QColor(80, 220, 80, 120)
                        elif err_n <= tol_n * mult.get("yellow", 1.5):
                            color = QColor(230, 210, 0, 120)
                        elif err_n <= tol_n * mult.get("orange", 2.0):
                            color = QColor(230, 140, 0, 120)
                        else:
                            color = QColor(220, 0, 0, 120)
                        try:
                            canvas.set_live_cell_color(int(r), int(c), color)
                        except Exception:
                            pass
                    pcts_sorted = sorted(pcts)
                    metrics["count"] = len(pcts)
                    metrics["mean_pct"] = sum(pcts) / len(pcts) if pcts else 0.0
                    metrics["median_pct"] = pcts_sorted[len(pcts_sorted)//2] if pcts_sorted else 0.0
                    metrics["max_pct"] = max(pcts) if pcts else 0.0
                    metrics["signed_bias_pct"] = sum(signed) / len(signed) if signed else 0.0
                    return metrics
                base_metrics = {}
                sel_metrics = {}
                if base_raw:
                    base_metrics = _paint(_replay(base_raw), self.canvas_left)
                if sel_raw:
                    sel_metrics = _paint(_replay(sel_raw), self.canvas_right)
                # Update metrics pane
                try:
                    self.controls.temperature_testing_panel.lbl_base_cnt.setText(str(int(base_metrics.get("count", 0))))
                    self.controls.temperature_testing_panel.lbl_base_mean.setText(f"{float(base_metrics.get('mean_pct', 0.0)):.1f}%")
                    self.controls.temperature_testing_panel.lbl_base_med.setText(f"{float(base_metrics.get('median_pct', 0.0)):.1f}%")
                    self.controls.temperature_testing_panel.lbl_base_max.setText(f"{float(base_metrics.get('max_pct', 0.0)):.1f}%")
                    self.controls.temperature_testing_panel.lbl_sel_cnt.setText(str(int(sel_metrics.get("count", 0))))
                    self.controls.temperature_testing_panel.lbl_sel_mean.setText(f"{float(sel_metrics.get('mean_pct', 0.0)):.1f}%")
                    self.controls.temperature_testing_panel.lbl_sel_med.setText(f"{float(sel_metrics.get('median_pct', 0.0)):.1f}%")
                    self.controls.temperature_testing_panel.lbl_sel_max.setText(f"{float(sel_metrics.get('max_pct', 0.0)):.1f}%")
                except Exception:
                    pass
                stage_rendered = True
            except Exception:
                stage_rendered = False
        # Re-ingest with filter (non-stage/all fallback) and set keys
        if not stage_rendered:
            try:
                if base_raw:
                    self._ingest_processed_csv("TEMP OFF", base_raw, t0, t1)
                    self._temp_baseline_key = f"{base_raw}#t:{t0 or ''}-{t1 or ''}" if (t0 or t1) else base_raw
                if sel_raw:
                    self._ingest_processed_csv("TEMP ON", sel_raw, t0, t1)
                    self._temp_selected_key = f"{sel_raw}#t:{t0 or ''}-{t1 or ''}" if (t0 or t1) else sel_raw
            except Exception:
                pass
        # Apply to canvases
        try:
            if getattr(self, "_temp_view_mode", "Heatmap") == "Grid View":
                # Grid view: show grids; colors already managed in ingestion outputs to metrics only
                if self._temp_baseline_key:
                    self.canvas_left.set_heatmap_points([])
                    self.canvas_left.show_live_grid(*getattr(config, "GRID_DIMS_BY_MODEL", {}).get(self.state.selected_device_type or "06", (3, 3)))
                if self._temp_selected_key:
                    self.canvas_right.set_heatmap_points([])
                    self.canvas_right.show_live_grid(*getattr(config, "GRID_DIMS_BY_MODEL", {}).get(self.state.selected_device_type or "06", (3, 3)))
            else:
                if self._temp_baseline_key:
                    pts = self._heatmaps.get(self._temp_baseline_key, [])
                    self.canvas_left.set_heatmap_points(pts)
                if self._temp_selected_key:
                    pts2 = self._heatmaps.get(self._temp_selected_key, [])
                    self.canvas_right.set_heatmap_points(pts2)
                try:
                    self.top_tabs_left.setCurrentWidget(self.canvas_left)
                    self.top_tabs_right.setCurrentWidget(self.canvas_right)
                except Exception:
                    pass
            # Metrics pane update (if available)
            def _metrics_for(key: Optional[str]) -> dict:
                if not key:
                    return {}
                return dict(self._heatmap_metrics.get(key, {}))
            if not stage_rendered:
                base_m = _metrics_for(getattr(self, "_temp_baseline_key", None))
                sel_m = _metrics_for(getattr(self, "_temp_selected_key", None))
                try:
                    self.controls.temperature_testing_panel.lbl_base_cnt.setText(str(int(base_m.get("count") or 0)))
                    self.controls.temperature_testing_panel.lbl_base_mean.setText(f"{float(base_m.get('mean_pct') or 0.0):.1f}%")
                    self.controls.temperature_testing_panel.lbl_base_med.setText(f"{float(base_m.get('median_pct') or 0.0):.1f}%")
                    self.controls.temperature_testing_panel.lbl_base_max.setText(f"{float(base_m.get('max_pct') or 0.0):.1f}%")
                    self.controls.temperature_testing_panel.lbl_sel_cnt.setText(str(int(sel_m.get("count") or 0)))
                    self.controls.temperature_testing_panel.lbl_sel_mean.setText(f"{float(sel_m.get('mean_pct') or 0.0):.1f}%")
                    self.controls.temperature_testing_panel.lbl_sel_med.setText(f"{float(sel_m.get('median_pct') or 0.0):.1f}%")
                    self.controls.temperature_testing_panel.lbl_sel_max.setText(f"{float(sel_m.get('max_pct') or 0.0):.1f}%")
                    try:
                        self._log(f"apply_views_done: base={{cnt:{int(base_m.get('count') or 0)}, mean:{float(base_m.get('mean_pct') or 0.0):.1f}%}} sel={{cnt:{int(sel_m.get('count') or 0)}, mean:{float(sel_m.get('mean_pct') or 0.0):.1f}%}}")
                    except Exception:
                        pass
                except Exception:
                    pass
        except Exception:
            pass

    def on_temp_process(self, slot: Callable[[str, str, str, dict], None]) -> None:
        self._on_temp_process_cb = slot

    def on_apply_temperature_correction(self, slot: Callable[[], None]) -> None:
        self._on_apply_temp_corr_cb = slot

    def _on_end_session_clicked(self) -> None:
        # Submit whatever progress exists, using the active model id display
        if self._live_session is None:
            self._on_live_end()
            return
        # Confirm with the user before ending
        try:
            box = QtWidgets.QMessageBox(self)
            box.setWindowTitle("End Session")
            box.setText("Are you sure you want to end the session?")
            confirm_btn = box.addButton("Confirm", QtWidgets.QMessageBox.AcceptRole)
            back_btn = box.addButton("Go Back", QtWidgets.QMessageBox.RejectRole)
            box.setIcon(QtWidgets.QMessageBox.Question)
            box.exec()
            if box.clickedButton() is not confirm_btn:
                # User chose Go Back; do nothing, preserving live state
                return
        except Exception:
            # On any dialog error, fall back to not ending automatically
            return
        self._submit_results_and_end()

    # --- CSV export directory handling ---
    def _get_last_csv_dir(self) -> str:
        try:
            settings = QtCore.QSettings("Axioforce", "AxioforceFluxLite")
            val = str(settings.value("csvExport/lastDir", "") or "").strip()
            if val:
                return val
        except Exception:
            pass
        # Fallback to directory from config default path
        try:
            import os
            path = getattr(config, "CSV_EXPORT_PATH", "") or ""
            if path:
                d = os.path.dirname(path)
                if d:
                    return d
        except Exception:
            pass
        return ""

    def _set_last_csv_dir(self, directory: str) -> None:
        try:
            settings = QtCore.QSettings("Axioforce", "AxioforceFluxLite")
            settings.setValue("csvExport/lastDir", str(directory or ""))
        except Exception:
            pass

    def _get_last_export_file(self) -> str:
        try:
            settings = QtCore.QSettings("Axioforce", "AxioforceFluxLite")
            val = str(settings.value("export/lastFile", "") or "").strip()
            if val:
                return val
        except Exception:
            pass
        return ""

    def _set_last_export_file(self, file_path: str) -> None:
        try:
            settings = QtCore.QSettings("Axioforce", "AxioforceFluxLite")
            settings.setValue("export/lastFile", str(file_path or ""))
        except Exception:
            pass

    # Model management UI handlers
    def _on_package_model_clicked(self) -> None:
        dlg = ModelPackagerDialog(self)
        if dlg.exec() == ModelPackagerDialog.Accepted:
            force_dir, moments_dir, output_dir = dlg.get_values()
            payload = {
                "forceModelDir": force_dir,
                "momentsModelDir": moments_dir,
                "outputDir": output_dir,
            }
            if self._on_package_model_cb:
                try:
                    self._on_package_model_cb(payload)
                except Exception:
                    pass

    def _on_model_metadata(self, data: object) -> None:
        # Expect list of model metadata dicts; populate list and show active model, else "No Model"
        try:
            models = list(data or [])
        except Exception:
            models = []
        # Log summary with selected device context
        try:
            dev = (self.state.selected_device_id or "").strip()
            parts: list[str] = []
            for m in models:
                try:
                    mid = str((m or {}).get("modelId") or (m or {}).get("model_id") or "?")
                    loc = str((m or {}).get("location") or "").strip() or "-"
                    act = bool((m or {}).get("active")) or bool((m or {}).get("model_active"))
                    parts.append(f"{mid}({'on' if act else 'off'},{loc})")
                except Exception:
                    continue
            summary = ", ".join(parts)
            self._log(f"model_metadata: dev={dev} count={len(models)} entries=[{summary}]")
        except Exception:
            pass
        # Populate list
        try:
            if hasattr(self.controls, "live_testing_panel"):
                self.controls.live_testing_panel.set_model_list(models)
        except Exception:
            pass
        # Determine current model: any entry with active flag true
        current_text = "No Model"
        try:
            active_entry = None
            for m in models:
                try:
                    is_active = self._is_model_entry_active(m)
                    if is_active:
                        active_entry = m
                        break
                except Exception:
                    continue
            if active_entry is not None:
                mid = (active_entry or {}).get("modelId") or (active_entry or {}).get("model_id") or "No Model"
                current_text = str(mid)
        except Exception:
            pass
        try:
            dev = (self.state.selected_device_id or "").strip()
            self._log(f"model_metadata: dev={dev} chosen='{current_text}'")
        except Exception:
            pass
        try:
            self.controls.live_testing_panel.set_current_model(current_text)
            # Keep Session Info pane's Model ID in sync with the active model label
            self.controls.live_testing_panel.set_session_model_id(current_text)
            # Remember for summary/export
            self._active_model_label = (current_text or "").strip() or None
            # If temperature testing requested metadata for a device, set label there too
            try:
                if hasattr(self.controls, "temperature_testing_panel") and self._temp_meta_device_id is not None:
                    self.controls.temperature_testing_panel.set_model_label(current_text)
                    self._temp_meta_device_id = None
            except Exception:
                pass
            # Update calibration gating based on new model
            self._update_calibration_enabled()
        except Exception:
            pass

    def _on_model_package_status(self, status: object) -> None:
        # Show a minimal status dialog
        try:
            s = status or {}
            st = str(s.get("status") or "").strip() if isinstance(s, dict) else str(s)
            msg = str(s.get("message") or "") if isinstance(s, dict) else ""
            QtWidgets.QMessageBox.information(self, "Package Model", f"Status: {st}\n{msg}")
        except Exception:
            pass

    def _on_model_activation_status(self, status: object) -> None:
        # After activation/deactivation, refresh current model (twice with a short delay)
        try:
            s = status or {}
            st = str(s.get("status") or "").strip() if isinstance(s, dict) else str(s)
            msg = str(s.get("message") or "") if isinstance(s, dict) else ""
            self._log(f"model_activation_status: status={st} msg='{msg}'")
        except Exception:
            pass
        try:
            dev_id = (self.state.selected_device_id or "").strip()
            if dev_id and self._request_model_metadata_cb:
                self._request_model_metadata_cb(dev_id)
                # Backend may commit asynchronously; refresh again shortly
                try:
                    QtCore.QTimer.singleShot(350, lambda: self._request_model_metadata_cb(dev_id))
                except Exception:
                    pass
            # Update status label from status and re-enable controls
            try:
                text = f"{st.capitalize()}" + (f": {msg}" if msg else "")
                self.controls.live_testing_panel.set_model_status(text)
            except Exception:
                pass
            self.controls.live_testing_panel.set_model_controls_enabled(True)
            # Recompute calibration gating
            self._update_calibration_enabled()
        except Exception:
            pass

    # --- Calibration: Load 45V test CSV ---
    def _on_load_45v(self) -> None:
        # Guard: ensure enabled state
        try:
            self.controls.live_testing_panel.set_calibration_status("Select folder…")
        except Exception:
            pass
        try:
            options = QtWidgets.QFileDialog.Options()
            start_dir = self._get_last_csv_dir() or QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.DocumentsLocation)
            directory = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose Calibration Folder", start_dir, options=options)
        except Exception:
            directory = ""
        if not directory:
            try:
                self.controls.live_testing_panel.set_calibration_status("—")
            except Exception:
                pass
            return
        # Remember directory
        try:
            self._set_last_csv_dir(directory)
        except Exception:
            pass
        # Find candidate files with '45V', 'OLS', 'TLS' in name (case-insensitive) and .csv
        import os
        cand_45v: list[str] = []
        cand_ols: list[str] = []
        cand_tls: list[str] = []
        try:
            for name in os.listdir(directory):
                try:
                    low = name.lower()
                    full = os.path.join(directory, name)
                    if not low.endswith(".csv"):
                        continue
                    if "45v" in low:
                        cand_45v.append(full)
                    elif "ols" in low:
                        cand_ols.append(full)
                    elif "tls" in low:
                        cand_tls.append(full)
                except Exception:
                    continue
        except Exception:
            cand_45v, cand_ols, cand_tls = [], [], []
        if not (cand_45v or cand_ols or cand_tls):
            try:
                QtWidgets.QMessageBox.information(self, "Calibration", "No 45V/OLS/TLS CSV files found in the selected folder.")
                self.controls.live_testing_panel.set_calibration_status("No calibration files found")
            except Exception:
                pass
            return
        # Choose first match for each type (simple heuristic). Future: prompt if multiple.
        chosen_45v = cand_45v[0] if cand_45v else ""
        chosen_ols = cand_ols[0] if cand_ols else ""
        chosen_tls = cand_tls[0] if cand_tls else ""
        # Update status label and enable generate
        try:
            # Compact status with checks in preferred order
            checks: list[str] = []
            # Use green checks via HTML span
            check_sym = '<span style="color:#2ecc71;">✓</span>'
            if chosen_45v:
                checks.append(f"45V {check_sym}")
            if chosen_tls:
                checks.append(f"TLS {check_sym}")
            if chosen_ols:
                checks.append(f"OLS {check_sym}")
            status = ("Loaded: " + "  ".join(checks)) if checks else "Loaded: —"
            self.controls.live_testing_panel.set_calibration_status(status)
            self.controls.live_testing_panel.set_generate_enabled(True)
        except Exception:
            pass
        # Store for processing
        try:
            self._cal_45v_path = chosen_45v
            self._cal_ols_path = chosen_ols
            self._cal_tls_path = chosen_tls
        except Exception:
            pass
        # If backend-processed files already exist in calibration_output, remember them
        try:
            import os as _os
            out_dir = ""
            for key, src in (("45v", chosen_45v), ("ols", chosen_ols), ("tls", chosen_tls)):
                if not src:
                    setattr(self, f"_cal_{key}_processed", "")
                    continue
                base = _os.path.splitext(_os.path.basename(src))[0]
                out_dir = _os.path.join(_os.path.dirname(src), "calibration_output")
                existing = ""
                if _os.path.isdir(out_dir):
                    for name in _os.listdir(out_dir):
                        low = name.lower()
                        if base.lower() in low and ("__processed" in low or "processed" in low) and low.endswith(".csv"):
                            existing = _os.path.join(out_dir, name)
                            break
                setattr(self, f"_cal_{key}_processed", existing or "")
        except Exception:
            pass

    def _on_generate_heatmap(self) -> None:
        # Require at least one selected calibration file
        p45 = getattr(self, "_cal_45v_path", "") or ""
        pols = getattr(self, "_cal_ols_path", "") or ""
        ptls = getattr(self, "_cal_tls_path", "") or ""
        if not (p45 or pols or ptls):
            try:
                QtWidgets.QMessageBox.information(self, "Calibration", "Please load a calibration folder with 45V/OLS/TLS CSVs first.")
            except Exception:
                pass
            return
        model_id = (getattr(self, "_active_model_label", None) or "").strip() or (self.state.selected_device_type or "06")
        plate_type = (self.state.selected_device_type or "").strip() or "06"
        device_id = (self.state.selected_device_id or "").strip()
        # Import processor with fallback (relative then absolute), log any errors
        process_45v = None
        process_ols = None
        process_tls = None
        try:
            from ..calibration.processor import process_45v, process_ols, process_tls  # type: ignore
        except Exception as e1:
            try:
                from calibration.processor import process_45v, process_ols, process_tls  # type: ignore
            except Exception as e2:
                try:
                    print(f"[calib] import processor failed: rel={e1} abs={e2}")
                except Exception:
                    pass
        try:
            self.controls.live_testing_panel.set_calibration_status("Processing…")
        except Exception:
            pass
        # Process each available test independently
        summary_msgs: list[str] = []
        statuses: dict[str, bool] = {}
        debug_series: list[dict] = []
        def _run_one(tag: str, src_path: str, processed_hint_attr: str, fn) -> None:
            if not src_path or not callable(fn):
                return
            try:
                existing_processed = str(getattr(self, processed_hint_attr, "") or "").strip() or None
            except Exception:
                existing_processed = None
            try:
                res = fn(src_path, model_id, plate_type, device_id, existing_processed)
            except Exception as e:
                res = {"error": str(e)}
            if isinstance(res, dict) and not res.get("error"):
                pts = res.get("points") or []
                metrics = res.get("metrics") or {}
                processed_csv = str(res.get("processed_csv") or "").strip()
                dbg = res.get("debug") or {}
                # Map to tuples for canvas
                try:
                    tuples = [(float(p.get("x_mm", 0.0)), float(p.get("y_mm", 0.0)), str(p.get("bin", "green"))) for p in pts]
                except Exception:
                    tuples = []
                try:
                    # Add to store; don't overwrite existing entries for other files
                    if processed_csv:
                        base = processed_csv.split("/")[-1] if "/" in processed_csv else processed_csv.split("\\")[-1]
                        label = base or processed_csv
                    else:
                        label = f"{tag} heatmap"
                        processed_csv = f"__{tag}__"
                    try:
                        self._heatmaps
                    except Exception:
                        self._heatmaps = {}
                    try:
                        self._heatmap_metrics
                    except Exception:
                        self._heatmap_metrics = {}
                    try:
                        self._heatmap_points_raw
                    except Exception:
                        self._heatmap_points_raw = {}
                    self._heatmaps[processed_csv] = tuples
                    # Store raw points for grid view and percent metrics
                    self._heatmap_points_raw[processed_csv] = [
                        {
                            "x_mm": float(p.get("x_mm", 0.0)),
                            "y_mm": float(p.get("y_mm", 0.0)),
                            "ratio": float(p.get("ratio", 0.0)),
                            "abs_pct": float(p.get("abs_pct", 0.0)),
                            "signed_pct": float(p.get("signed_pct", 0.0)),
                        }
                        for p in (pts or [])
                    ]
                    cnt = int(metrics.get("count", 0))
                    # Store full metrics as provided (includes N and %)
                    self._heatmap_metrics[processed_csv] = dict(metrics)
                    summary_msgs.append(f"{tag}: {cnt} pts, mean {float(metrics.get('mean_err', 0.0)):.1f} N ({float(metrics.get('mean_pct', 0.0)):.1f}%)")
                    statuses[tag] = True
                    # Keep debug for combined plotting
                    try:
                        if isinstance(dbg, dict) and dbg.get("t_ms"):
                            dcopy = dict(dbg)
                            dcopy["tag"] = tag
                            debug_series.append(dcopy)
                    except Exception:
                        pass
                except Exception:
                    pass
            else:
                try:
                    err = (res or {}).get("error") if isinstance(res, dict) else "processing_failed"
                    summary_msgs.append(f"{tag}: failed ({err})")
                    statuses[tag] = False
                except Exception:
                    pass

        _run_one("45V", p45, "_cal_45v_processed", process_45v)
        _run_one("OLS", pols, "_cal_ols_processed", process_ols)
        _run_one("TLS", ptls, "_cal_tls_processed", process_tls)
        # Update canvas to last processed (prefer 45V if available, else OLS, else TLS)
        try:
            key_order = []
            # Build keys in same order as processed
            for tag, src in (("45V", p45), ("OLS", pols), ("TLS", ptls)):
                if not src:
                    continue
                # Attempt to find stored key by matching base name
                import os as _os
                out_dir = _os.path.join(_os.path.dirname(src), "calibration_output")
                base = _os.path.splitext(_os.path.basename(src))[0]
                chosen_key = ""
                for k in (self._heatmaps or {}).keys():
                    if base.lower() in k.lower():
                        chosen_key = k
                        break
                if chosen_key:
                    key_order.append(chosen_key)
            if key_order:
                last_key = key_order[-1]
                self._apply_heatmap_or_grid(last_key)
        except Exception:
            pass
        # Rebuild list including All Heatmaps
        try:
            self._rebuild_heatmap_list()
        except Exception:
            pass
        # Status summary
        try:
            # Build concise generated status line with checks and xs (red) per attempted test
            tokens: list[str] = []
            green = '<span style="color:#2ecc71;">✓</span>'
            red = '<span style="color:#e74c3c;">✗</span>'
            for tag, src in (("45V", p45), ("TLS", ptls), ("OLS", pols)):
                if not src:
                    continue
                ok = statuses.get(tag, False)
                sym = green if ok else red
                tokens.append(f"{tag} {sym}")
            status_line = "Heatmaps Generated: " + ("  ".join(tokens) if tokens else "—")
            self.controls.live_testing_panel.set_calibration_status(status_line)
        except Exception:
            pass
        # Optional combined debug plot: 3 panels (45V, OLS, TLS) with window markers
        try:
            import os as _os
            if str(_os.environ.get("AXIO_CALIB_PLOT", "0")).strip() == "1" and debug_series:
                self._plot_calibration_debug_multi(debug_series)
        except Exception:
            pass

    def _plot_calibration_debug_multi(self, series_list: list[dict]) -> None:
        try:
            import matplotlib.pyplot as plt
        except Exception:
            return
        # Keep consistent ordering: 45V, OLS, TLS if present
        order = ["45V", "OLS", "TLS"]
        ordered = []
        for name in order:
            for d in (series_list or []):
                if str(d.get("tag") or "").upper() == name:
                    ordered.append(d)
                    break
        # Append any others not matched
        for d in (series_list or []):
            if d not in ordered:
                ordered.append(d)
        if not ordered:
            return
        rows = len(ordered)
        fig, axes = plt.subplots(rows, 1, sharex=False, figsize=(10, max(3 * rows, 6)))
        if rows == 1:
            axes = [axes]
        for ax, d in zip(axes, ordered):
            t = list(d.get("t_ms") or [])
            if not t:
                continue
            t0 = t[0]
            ts = [((x - t0) / 1000.0) for x in t]
            bz = list(d.get("bz") or [])
            sumz = list(d.get("sum_z") or [])
            bzs = list(d.get("bz_smooth") or [])
            wins = list(d.get("windows_idx") or [])
            ax.plot(ts, bz, label="bz (truth)", linewidth=1.0, color="#1f77b4")
            ax.plot(ts, sumz, label="sum-z (model)", linewidth=1.0, color="#ff7f0e")
            if bzs and len(bzs) == len(bz):
                ax.plot(ts, bzs, label="bz_smooth", linewidth=1.8, color="#2ca02c")
            for (i0, j) in wins:
                if 0 <= i0 < len(ts) and 0 < j <= len(ts):
                    ax.axvline(ts[i0], color="#2ca02c", linestyle="--", alpha=0.6)
                    ax.axvline(ts[j - 1], color="#d62728", linestyle="--", alpha=0.6)
            tag = str(d.get("tag") or "")
            ax.set_title(f"{tag} (windows={len(wins)})")
            ax.set_ylabel("N")
            ax.grid(True, alpha=0.3)
            ax.legend(loc="upper right")
        axes[-1].set_xlabel("Time (s)")
        fig.tight_layout()
        try:
            plt.show(block=False)
        except TypeError:
            plt.show()

    def _on_heatmap_selected(self, key: str) -> None:
        self._apply_heatmap_or_grid(key)

    def _rebuild_heatmap_list(self) -> None:
        try:
            items = list((self._heatmaps or {}).keys())
        except Exception:
            items = []
        try:
            self.controls.live_testing_panel.clear_heatmap_entries()
        except Exception:
            pass
        # Build combined '__ALL__'
        combined: list[tuple[float, float, str]] = []
        combined_raw: list[dict] = []
        total_count = 0
        total_mean_num = 0.0
        medians: list[float] = []
        maxes: list[float] = []
        for k, pts in (self._heatmaps or {}).items():
            if not isinstance(pts, list):
                continue
            combined.extend(pts)
            # Combine raw points (may be empty)
            try:
                for rp in (self._heatmap_points_raw or {}).get(k, []):
                    combined_raw.append(dict(rp))
            except Exception:
                pass
            m = (self._heatmap_metrics or {}).get(k) or {}
            c = int(m.get("count", 0))
            total_count += c
            total_mean_num += float(m.get("mean_err", 0.0)) * max(0, c)
            medians.append(float(m.get("median_err", 0.0)))
            maxes.append(float(m.get("max_err", 0.0)))
        if combined:
            # Store combined
            self._heatmaps["__ALL__"] = combined
            self._heatmap_points_raw["__ALL__"] = combined_raw
            # Compute percent-based combined metrics from raw points
            abs_pcts = [float(r.get("abs_pct", 0.0)) for r in combined_raw if isinstance(r, dict)]
            signed_pcts = [float(r.get("signed_pct", 0.0)) for r in combined_raw if isinstance(r, dict)]
            mean_pct_all = (sum(abs_pcts) / len(abs_pcts)) if abs_pcts else 0.0
            try:
                abs_pcts_sorted = sorted(abs_pcts)
                median_pct_all = abs_pcts_sorted[len(abs_pcts_sorted)//2] if abs_pcts_sorted else 0.0
            except Exception:
                median_pct_all = 0.0
            max_pct_all = max(abs_pcts) if abs_pcts else 0.0
            bias_pct_all = (sum(signed_pcts) / len(signed_pcts)) if signed_pcts else 0.0
            # Keep count; omit N-based summaries to avoid misleading across tests
            self._heatmap_metrics["__ALL__"] = {
                "count": total_count,
                "mean_pct": mean_pct_all,
                "median_pct": median_pct_all,
                "max_pct": max_pct_all,
                "signed_bias_pct": bias_pct_all,
            }
            try:
                self.controls.live_testing_panel.add_heatmap_entry("All Heatmaps", "__ALL__", total_count)
            except Exception:
                pass
        # Add individual entries
        for k in items:
            try:
                base = k.split("/")[-1] if "/" in k else k.split("\\")[-1]
                c = int((self._heatmap_metrics or {}).get(k, {}).get("count", 0))
                # Short label based on test type in filename
                low = base.lower()
                if "45v" in low:
                    short = "45V"
                elif "ols" in low:
                    short = "OLS"
                elif "tls" in low:
                    short = "TLS"
                else:
                    short = "HM"
                self.controls.live_testing_panel.add_heatmap_entry(short, k, c)
            except Exception:
                continue

    def _on_calibration_cell_clicked(self, row: int, col: int) -> None:
        # Only when not in a live session and in Grid View
        if getattr(self, "_live_session", None) is not None:
            return
        try:
            if self.controls.live_testing_panel.current_heatmap_view() != "Grid View":
                return
            item = self.controls.live_testing_panel.heatmap_list.currentItem()
            key = item.data(QtCore.Qt.UserRole) if item is not None else None
            if not key:
                return
            raw = (self._heatmap_points_raw or {}).get(str(key)) or []
            # Compute per-cell ratios (reuse mapping)
            dev_type = (self.state.selected_device_type or "06").strip()
            rows, cols = getattr(config, "GRID_DIMS_BY_MODEL", {}).get(dev_type, (3, 3))
            signed_pcts: list[float] = []
            for pt in raw:
                try:
                    x_mm = float(pt.get("x_mm", 0.0))
                    y_mm = float(pt.get("y_mm", 0.0))
                    sp = float(pt.get("signed_pct", 0.0))
                except Exception:
                    continue
                cell = self._cell_from_mm(x_mm, y_mm, dev_type, int(rows), int(cols))
                if cell == (int(row), int(col)):
                    signed_pcts.append(sp)
            count = len(signed_pcts)
            avg_signed = (sum(signed_pcts) / count) if count else 0.0
            # Show on-grid overlay status and highlight the clicked cell
            try:
                self.canvas_right.set_live_active_cell(int(row), int(col))
            except Exception:
                pass
            try:
                sign = "+" if avg_signed >= 0 else ""
                status = f"Signed Error: {sign}{avg_signed:.1f}%\nCount: {count}"
                self.canvas_right.set_live_status(status)
            except Exception:
                pass
        except Exception:
            pass

    def _on_heatmap_view_changed(self, _mode: str) -> None:
        # Re-apply current selection with new view mode
        try:
            item = self.controls.live_testing_panel.heatmap_list.currentItem()
            key = item.data(QtCore.Qt.UserRole) if item is not None else None
            if key:
                self._apply_heatmap_or_grid(str(key))
        except Exception:
            pass

    def _apply_heatmap_or_grid(self, key: str) -> None:
        try:
            mode = self.controls.live_testing_panel.current_heatmap_view()
        except Exception:
            mode = "Heatmap"
        # Clear any prior grid selection/status on selection change
        try:
            self.canvas_right.set_live_active_cell(None, None)
            self.canvas_right.set_live_status(None)
        except Exception:
            pass
        # Update metrics area
        try:
            m = (self._heatmap_metrics or {}).get(str(key)) or {}
            self.controls.live_testing_panel.set_heatmap_metrics(dict(m), is_all=(str(key) == "__ALL__"))
        except Exception:
            pass
        if mode == "Grid View":
            self._apply_grid_view(key)
        else:
            # Heatmap
            tuples = (self._heatmaps or {}).get(str(key)) or []
            try:
                self.canvas_right.hide_live_grid()
            except Exception:
                pass
            try:
                self.canvas_right.set_heatmap_points(tuples)
                self.top_tabs_right.setCurrentWidget(self.canvas_right)
            except Exception:
                pass

    def _apply_grid_view(self, key: str) -> None:
        raw = (self._heatmap_points_raw or {}).get(str(key)) or []
        # Determine grid dims by device type
        dev_type = (self.state.selected_device_type or "06").strip()
        rows, cols = getattr(config, "GRID_DIMS_BY_MODEL", {}).get(dev_type, (3, 3))
        # Clear heatmap blobs and show grid
        try:
            self.canvas_right.set_heatmap_points([])
            self.canvas_right.show_live_grid(int(rows), int(cols))
        except Exception:
            pass
        # Aggregate ratios per cell
        per_cell: dict[tuple[int, int], list[float]] = {}
        for pt in raw:
            try:
                x_mm = float(pt.get("x_mm", 0.0))
                y_mm = float(pt.get("y_mm", 0.0))
                ratio = float(pt.get("ratio", 0.0))
            except Exception:
                continue
            cell = self._cell_from_mm(x_mm, y_mm, dev_type, int(rows), int(cols))
            if cell is None:
                continue
            per_cell.setdefault(cell, []).append(ratio)
        # Color cells using ratio vs multipliers
        mult = getattr(config, "COLOR_BIN_MULTIPLIERS", {
            "green": 1.0, "light_green": 1.25, "yellow": 1.5, "orange": 2.0, "red": 1e9
        })
        from PySide6.QtGui import QColor
        for r in range(int(rows)):
            for c in range(int(cols)):
                vals = per_cell.get((r, c))
                if not vals:
                    # clear/no color
                    try:
                        self.canvas_right.clear_live_cell_color(r, c)
                    except Exception:
                        pass
                    continue
                avg_ratio = sum(vals) / len(vals)
                if avg_ratio <= mult.get("green", 1.0):
                    color = QColor(0, 200, 0, 120)
                elif avg_ratio <= mult.get("light_green", 1.25):
                    color = QColor(80, 220, 80, 120)
                elif avg_ratio <= mult.get("yellow", 1.5):
                    color = QColor(230, 210, 0, 120)
                elif avg_ratio <= mult.get("orange", 2.0):
                    color = QColor(230, 140, 0, 120)
                else:
                    color = QColor(220, 0, 0, 120)
                try:
                    self.canvas_right.set_live_cell_color(r, c, color)
                except Exception:
                    pass
        try:
            self.top_tabs_right.setCurrentWidget(self.canvas_right)
        except Exception:
            pass

    def _cell_from_mm(self, x_mm_val: float, y_mm_val: float, dev_type: str, rows: int, cols: int) -> Optional[tuple[int, int]]:
        # Map COP (x_mm, y_mm) to grid cell using canonical device space (no rotation)
        rx, ry = x_mm_val, y_mm_val
        if dev_type == "06":
            w_mm = config.TYPE06_W_MM
            h_mm = config.TYPE06_H_MM
        elif dev_type == "07":
            w_mm = config.TYPE07_W_MM
            h_mm = config.TYPE07_H_MM
        elif dev_type == "11":
            w_mm = config.TYPE11_W_MM
            h_mm = config.TYPE11_H_MM
        else:
            w_mm = config.TYPE08_W_MM
            h_mm = config.TYPE08_H_MM
        half_w = w_mm / 2.0
        half_h = h_mm / 2.0
        if dev_type in ("07", "11"):
            if abs(rx) > half_w or abs(ry) > half_h:
                return None
            col_f = (rx + half_w) / w_mm * cols
            col_i = int(col_f)
            if col_i < 0:
                col_i = 0
            elif col_i >= cols:
                col_i = cols - 1
            t = (half_h - ry) / h_mm
            row_f = t * rows
            row_i = int(row_f)
        else:
            if abs(ry) > half_w or abs(rx) > half_h:
                return None
            col_f = (ry + half_w) / w_mm * cols
            col_i = int(col_f)
            if col_i < 0:
                col_i = 0
            elif col_i >= cols:
                col_i = cols - 1
            t = (half_h - rx) / h_mm
            row_f = t * rows
            row_i = int(row_f)
        if row_i < 0:
            row_i = 0
        elif row_i >= rows:
            row_i = rows - 1
        return (int(row_i), int(col_i))

    def _on_activate_model(self, model_id: str) -> None:
        dev_id = (self.state.selected_device_id or "").strip()
        if dev_id and self._on_activate_model_cb:
            try:
                self._on_activate_model_cb(dev_id, model_id)
            except Exception:
                pass
        # Ensure model bypass is disabled when activating a model
        try:
            if callable(getattr(self, "_on_set_model_bypass_cb", None)):
                self._on_set_model_bypass_cb(False)
                self._log("model_bypass_set:false on activate_model")
        except Exception:
            pass

    def _on_deactivate_model(self, model_id: str) -> None:
        dev_id = (self.state.selected_device_id or "").strip()
        if dev_id and self._on_deactivate_model_cb:
            try:
                self._on_deactivate_model_cb(dev_id, model_id)
            except Exception:
                pass

    # Removed: Google Sheets submission helper (replaced by Graph Excel writing)

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

    def _on_live_raw_payload(self, payload: object) -> None:
        """Handle raw backend payloads for Sensor View temperature and discrete temp averaging."""
        # First: always update Sensor View temperature smoothing for selected device (right Sensor View)
        try:
            self._update_sensor_temperature_from_payload(payload)
        except Exception:
            # Never let temperature UI updates break discrete temp buffering
            pass

        # Then: buffer raw payloads for discrete temp averaging (when in discrete temp live session)
        try:
            if self._live_session is None or not bool(getattr(self._live_session, "is_discrete_temp", False)):
                return
            if not isinstance(payload, dict):
                return
            dev_id = str(payload.get("deviceId") or payload.get("device_id") or "").strip()
            cur_dev = str(self.state.selected_device_id or "").strip()
            if not dev_id or not cur_dev or dev_id != cur_dev:
                return
            t_ms = int(payload.get("time") or 0)
            if t_ms <= 0:
                return
            buf = getattr(self, "_discrete_raw_buffer", None)
            if not isinstance(buf, list):
                buf = []
                self._discrete_raw_buffer = buf
            buf.append(payload)
            # Trim buffer to recent window (e.g., last 10 seconds)
            cutoff = t_ms - 10_000
            try:
                self._discrete_raw_buffer = [p for p in buf if int(p.get("time") or 0) >= cutoff]
            except Exception:
                # Best-effort trimming
                self._discrete_raw_buffer = buf[-5000:]
        except Exception:
            pass

    def _update_sensor_temperature_from_payload(self, payload: object) -> None:
        """Maintain a 15-second rolling average of avgTemperatureF for the selected device."""
        if not isinstance(payload, dict):
            return
        # Only track the currently selected device
        dev_id = str(payload.get("deviceId") or payload.get("device_id") or "").strip()
        cur_dev = str(self.state.selected_device_id or "").strip()
        if not dev_id or not cur_dev or dev_id != cur_dev:
            return
        t_ms = int(payload.get("time") or 0)
        if t_ms <= 0:
            return
        # Prefer explicit avgTemperatureF field; fall back to mean of per-sensor temperatureF if needed
        temp_val = payload.get("avgTemperatureF", None)
        try:
            temp_f = float(temp_val) if temp_val is not None else None
        except Exception:
            temp_f = None
        if temp_f is None:
            try:
                sensors = payload.get("sensors") or []
                temps = []
                for s in sensors:
                    try:
                        if "temperatureF" in s:
                            temps.append(float(s.get("temperatureF")))
                    except Exception:
                        continue
                if temps:
                    temp_f = sum(temps) / float(len(temps))
            except Exception:
                temp_f = None
        if temp_f is None:
            return
        # Append to rolling buffer and trim to last 15 seconds
        buf = self._sensor_temp_buffer
        buf.append((t_ms, float(temp_f)))
        cutoff = t_ms - 15_000
        while buf and buf[0][0] < cutoff:
            buf.pop(0)
        if not buf:
            self._sensor_temp_smoothed_f = None
            try:
                self.sensor_plot_right.set_temperature_f(None)
            except Exception:
                pass
            return
        avg = sum(v for _, v in buf) / float(len(buf))
        self._sensor_temp_smoothed_f = avg
        # Update only the right Sensor View, as requested
        try:
            self.sensor_plot_right.set_temperature_f(avg)
        except Exception:
            pass

    def _on_live_single_snapshot(self, snap: Optional[Tuple[float, float, float, int, bool, float, float]]) -> None:
        if self._live_session is None or snap is None:
            return
        try:
            x_mm, y_mm, fz_n, t_ms, is_visible, raw_x_mm, raw_y_mm = snap
        except Exception:
            return
        # For temperature tests, perform any pending stage switch only once load is off (Fz < 50 N)
        try:
            self._temp_maybe_perform_pending_stage_switch(float(fz_n), int(t_ms))
        except Exception:
            pass
        # Track latest backend time for stage marks and start current stage if pending
        try:
            self._last_snapshot_time_ms = int(t_ms)
            if self._stage_mark_pending_start and self._stage_mark_active_idx is not None:
                try:
                    stage = self._live_session.stages[self._live_stage_idx]
                except Exception:
                    stage = None
                if stage is not None:
                    dev_id = (self._live_session.device_id or "").strip()
                    cap = str(getattr(self, "_capture_csv_name", "") or "")
                    meta_store.start_stage_mark(dev_id, cap, stage.name, int(stage.index), int(self._last_snapshot_time_ms))
                    # Also append this stage start to the temp-testing session meta JSON (if applicable)
                    try:
                        self._append_temp_stage_event(
                            stage_name=stage.name,
                            stage_index=int(stage.index),
                            started_at_ms=int(self._last_snapshot_time_ms),
                        )
                    except Exception:
                        pass
                    self._stage_mark_pending_start = False
        except Exception:
            pass

        # Evaluate scheduled tare guidance before modifying arming/stability state
        self._maybe_run_tare_guidance(fz_n, int(t_ms), bool(is_visible))
        # Pause arming/stabilization while tare dialog active
        if self._tare_active:
            # Keep telemetry updating but skip further processing
            return
        # (Live telemetry UI removed)

        # Do not process if not visible / below threshold
        if not is_visible:
            return
        # In discrete temp mode, ignore samples until warmup+tare have completed
        if self._live_session is not None and bool(getattr(self._live_session, "is_discrete_temp", False)) and not bool(getattr(self, "_discrete_ready_for_data", False)):
            return

        # Append sample and trim by time window (stability 1.0 s)
        try:
            fz_abs = float(abs(fz_n))
            self._recent_samples.append((int(t_ms), float(x_mm), float(y_mm), fz_abs))
            cutoff = int(t_ms) - self._stability_window_ms
            while self._recent_samples and self._recent_samples[0][0] < cutoff:
                self._recent_samples.pop(0)
        except Exception:
            return

        # Determine active cell based on current COP and target force proximity
        try:
            stage = self._live_session.stages[self._live_stage_idx]
        except Exception:
            return

        rows = self._live_session.grid_rows
        cols = self._live_session.grid_cols
        is_discrete = bool(getattr(self._live_session, "is_discrete_temp", False))
        # In discrete mode, if we are waiting for unload after DB stage, gate on Fz < 100 N
        if is_discrete and bool(getattr(self, "_discrete_waiting_for_unload", False)):
            try:
                if fz_abs < 100.0:
                    # Weight removed: run a tare before the next stage; stage index will advance on tare completion
                    self._discrete_waiting_for_unload = False
                    self._start_discrete_tare_sequence()
                # While waiting for unload (or tare), skip arming/stability
                return
            except Exception:
                return

        # Map COP (x_mm, y_mm) to grid cell using canonical device space (no rotation).
        # Note:
        # - For 06 and 08: width along world Y (columns), height along world X (rows)
        # - For 07: width along world X (columns), height along world Y (rows)
        def to_cell_mm(x_mm_val: float, y_mm_val: float) -> Optional[Tuple[int, int]]:
            # IMPORTANT: Do not rotate here; cell assignment is in canonical device space
            rx, ry = x_mm_val, y_mm_val
            dev_type = (self.state.selected_device_type or "06").strip()
            if dev_type == "06":
                w_mm = config.TYPE06_W_MM
                h_mm = config.TYPE06_H_MM
            elif dev_type == "07":
                w_mm = config.TYPE07_W_MM
                h_mm = config.TYPE07_H_MM
            elif dev_type == "11":
                w_mm = config.TYPE11_W_MM
                h_mm = config.TYPE11_H_MM
            else:
                w_mm = config.TYPE08_W_MM
                h_mm = config.TYPE08_H_MM
            half_w = w_mm / 2.0
            half_h = h_mm / 2.0
            # For discrete temp testing, require CoP to be within a central 5 cm diameter circle (2.5 cm radius)
            if is_discrete:
                max_r = 25.0  # mm
                r_val = (rx * rx + ry * ry) ** 0.5
                if r_val > max_r:
                    return None
            if dev_type in ("07", "11"):
                # 07: width along X, height along Y
                if abs(rx) > half_w or abs(ry) > half_h:
                    return None
                # Columns from X: left (-half_w)->0, right (+half_w)->cols-1
                col_f = (rx + half_w) / w_mm * cols
                col_i = int(col_f)
                if col_i < 0:
                    col_i = 0
                elif col_i >= cols:
                    col_i = cols - 1
                # Rows from Y: top (+half_h)->0, bottom (-half_h)->rows-1
                t = (half_h - ry) / h_mm
                row_f = t * rows
                row_i = int(row_f)
            else:
                # 06, 08: width along Y, height along X
                if abs(ry) > half_w or abs(rx) > half_h:
                    return None
                # Columns from Y
                col_f = (ry + half_w) / w_mm * cols
                col_i = int(col_f)
                if col_i < 0:
                    col_i = 0
                elif col_i >= cols:
                    col_i = cols - 1
                # Rows from X (top=+half_h)
                t = (half_h - rx) / h_mm
                row_f = t * rows
                row_i = int(row_f)
            if row_i < 0:
                row_i = 0
            elif row_i >= rows:
                row_i = rows - 1
            # Return direct indices; rotation already accounted for above
            return (row_i, col_i)

        # Fractions within plate footprint for debug (0..1). Column uses Y/width, Row uses X/height (top=0)
        dev_type = (self.state.selected_device_type or "06").strip()
        if dev_type == "06":
            w_mm = config.TYPE06_W_MM
            h_mm = config.TYPE06_H_MM
        elif dev_type == "07":
            w_mm = config.TYPE07_W_MM
            h_mm = config.TYPE07_H_MM
        elif dev_type == "11":
            w_mm = config.TYPE11_W_MM
            h_mm = config.TYPE11_H_MM
        else:
            w_mm = config.TYPE08_W_MM
            h_mm = config.TYPE08_H_MM
        # Debug: show canonical fractions (no rotation) matching assignment space
        rx_dbg, ry_dbg = x_mm, y_mm
        half_w = w_mm / 2.0
        half_h = h_mm / 2.0
        if dev_type in ("07", "11"):
            # 07: columns from X, rows from Y
            col_frac = (rx_dbg + half_w) / w_mm
            row_frac_top = (half_h - ry_dbg) / h_mm
        else:
            # 06, 08: columns from Y, rows from X
            col_frac = (ry_dbg + half_w) / w_mm
            row_frac_top = (half_h - rx_dbg) / h_mm

        cell = to_cell_mm(x_mm, y_mm)
        if cell is None:
            # In discrete mode, leaving the central circle should immediately disarm/reset
            if bool(getattr(self._live_session, "is_discrete_temp", False)):
                try:
                    self._arming_cell = None
                    self._arming_start_ms = None
                    self._active_cell = None
                    self._recent_samples.clear()
                    self.canvas_left.set_live_active_cell(None, None)
                    self.canvas_right.set_live_active_cell(None, None)
                    self.controls.live_testing_panel.set_debug_status("Arming… (stay inside center circle with ≥50 N)")
                except Exception:
                    pass
                return
            # Normal mode: not over the plate; reset arming guidance
            try:
                self.controls.live_testing_panel.set_debug_status("Move load onto plate area to arm…")
            except Exception:
                pass
            return
        row, col = cell
        try:
            # Show live cell/frac for debugging grid mapping
            self.controls.live_testing_panel.set_debug_status(
                f"Cell r={row} c={col} | col_frac={col_frac:.2f} row_frac(top)={row_frac_top:.2f} — Arming…"
            )
        except Exception:
            pass

        # Continuous arming: stay in same cell with |Fz| >= 50 N for 2.0 s
        if self._active_cell is None:
            try:
                if fz_abs >= 50.0:
                    if self._arming_cell == (row, col):
                        arm_span = int(t_ms) - int(self._arming_start_ms or int(t_ms))
                    else:
                        self._arming_cell = (row, col)
                        self._arming_start_ms = int(t_ms)
                        arm_span = 0
                    # Debug
                    try:
                        self.controls.live_testing_panel.set_debug_status(
                            f"Arming… cell r={row} c={col} | span={arm_span} / {self._arming_window_ms} ms (≥50 N)"
                        )
                    except Exception:
                        pass
                    # Skip arming if this cell already has a recorded result for this stage
                    try:
                        already_done = False
                        if stage is not None:
                            existing = stage.results.get((row, col))
                            already_done = bool(existing and existing.fz_mean_n is not None)
                    except Exception:
                        already_done = False
                    if not already_done and arm_span >= self._arming_window_ms:
                        self._active_cell = (row, col)
                        self._arming_cell = None
                        self._arming_start_ms = None
                        self._recent_samples.clear()  # start stability fresh
                        self.canvas_left.set_live_active_cell(row, col)
                        self.canvas_right.set_live_active_cell(row, col)
                        self.controls.live_testing_panel.set_debug_status("Armed: hold steady for 2.0 s…")
                        self._log(f"cell_armed: stage={stage.index}, loc={stage.location}, row={row}, col={col}")
                else:
                    # Below threshold resets arming
                    self._arming_cell = None
                    self._arming_start_ms = None
                    try:
                        self.controls.live_testing_panel.set_debug_status("Arming… (need ≥50 N for 2.0 s in one cell)")
                    except Exception:
                        pass
            except Exception:
                pass

        # If active, ensure COP stays within active cell during window
        if self._active_cell is not None:
            ar, ac = self._active_cell
            if (row, col) != (ar, ac):
                # Left the active cell; reset window
                self._recent_samples.clear()
                self._active_cell = None
                try:
                    self.canvas_left.set_live_active_cell(None, None)
                    self.canvas_right.set_live_active_cell(None, None)
                    self.controls.live_testing_panel.set_debug_status("Arming… (need ≥50 N for 2.0 s in one cell)")
                except Exception:
                    pass
                return

            # Check stability: Fz std <= 5 N and window length >= 1.0 s
            if not self._recent_samples:
                return
            t_span = self._recent_samples[-1][0] - self._recent_samples[0][0]
            required_ms = max(0, self._stability_window_ms - self._stability_tolerance_ms)
            if t_span < required_ms:
                try:
                    self.controls.live_testing_panel.set_debug_status(f"Stability… collecting {t_span} / {self._stability_window_ms} ms (tol {self._stability_tolerance_ms} ms)")
                except Exception:
                    pass
                return
            try:
                values = [s[3] for s in self._recent_samples]
                mean_fz = sum(values) / len(values)
                var = sum((v - mean_fz) ** 2 for v in values) / max(1, (len(values) - 1))
                std_fz = var ** 0.5
            except Exception:
                return
            try:
                self.controls.live_testing_panel.set_debug_status(f"Stability… window={t_span} ms | std={std_fz:.1f} N | mean={mean_fz:.1f} N")
            except Exception:
                pass
            if std_fz <= 5.0:
                # Capture reading
                try:
                    rkey = (ar, ac)
                    cell = stage.results.get(rkey)
                    if cell is not None and cell.fz_mean_n is None:
                        is_discrete = bool(getattr(self._live_session, "is_discrete_temp", False))
                        # For discrete temp sessions, require COP stability (within 3 cm radius) in addition to Fz
                        if is_discrete:
                            try:
                                if not self._discrete_xy_stable():
                                    try:
                                        self.controls.live_testing_panel.set_debug_status(
                                            "Stability… COP not stable yet — keep load centered."
                                        )
                                    except Exception:
                                        pass
                                    # Do not record or color this window; keep arming/stability active
                                    self._recent_samples.clear()
                                    self._active_cell = None
                                    self.canvas_left.set_live_active_cell(None, None)
                                    self.canvas_right.set_live_active_cell(None, None)
                                    return
                            except Exception:
                                # On any error in COP check, fall back to Fz-only behavior
                                pass

                        # At this point, stability criteria are satisfied (Fz, and COP for discrete)
                        cell.fz_mean_n = mean_fz
                        # Average COP in window
                        mean_x = sum(s[1] for s in self._recent_samples) / len(self._recent_samples)
                        mean_y = sum(s[2] for s in self._recent_samples) / len(self._recent_samples)
                        cell.cop_x_mm = mean_x
                        cell.cop_y_mm = mean_y
                        cell.error_n = abs(mean_fz - stage.target_n)

                        # Color binning based on model/test-specific threshold
                        err = cell.error_n or 0.0
                        from PySide6.QtGui import QColor
                        model_id = (self._live_session.model_id or "06").strip()
                        # Determine per-stage threshold (DB vs BW)
                        is_db = (stage.name.lower().find("db") >= 0)
                        base_tol = (
                            float(self._live_session.thresholds.dumbbell_tol_n)
                            if is_db else float(self._live_session.thresholds.bodyweight_tol_n)
                        )
                        if bool(getattr(self._live_session, "is_temp_test", False)):
                            color = QColor(160, 90, 255, 140)  # purple
                        else:
                            g = config.COLOR_BIN_MULTIPLIERS
                            if err <= base_tol * g["green"]:
                                color = QColor(0, 200, 0, 120)
                            elif err <= base_tol * g["light_green"]:
                                color = QColor(80, 220, 80, 120)
                            elif err <= base_tol * g["yellow"]:
                                color = QColor(220, 200, 0, 120)
                            elif err <= base_tol * g["orange"]:
                                color = QColor(230, 140, 0, 120)
                            else:
                                color = QColor(220, 0, 0, 120)
                        self.canvas_left.set_live_cell_color(ar, ac, color)
                        self.canvas_right.set_live_cell_color(ar, ac, color)

                        # Handle stable-window aggregation for discrete temp BEFORE clearing window
                        discrete_ok = True
                        if is_discrete:
                            try:
                                if self._recent_samples:
                                    window_start = int(self._recent_samples[0][0])
                                    window_end = int(self._recent_samples[-1][0])
                                    phase_kind = "45lb" if stage.name.lower().find("db") >= 0 else "bodyweight"
                                    discrete_ok = bool(
                                        self._accumulate_discrete_measurement(phase_kind, window_start, window_end)
                                    )
                            except Exception:
                                discrete_ok = False

                        # If discrete aggregation failed (e.g., no raw samples), do NOT advance or mark
                        # the stage as done. Revert this cell and ask the user to redo it.
                        if is_discrete and not discrete_ok:
                            try:
                                from PySide6 import QtWidgets as _QtW
                            except Exception:
                                _QtW = None
                            # Clear this cell's stored result so it does not count as completed
                            try:
                                cell.fz_mean_n = None
                                cell.cop_x_mm = None
                                cell.cop_y_mm = None
                                cell.error_n = None
                            except Exception:
                                pass
                            # Clear recent samples and active cell, then repaint stage context
                            try:
                                self._recent_samples.clear()
                            except Exception:
                                pass
                            try:
                                self._active_cell = None
                                self.canvas_left.set_live_active_cell(None, None)
                                self.canvas_right.set_live_active_cell(None, None)
                            except Exception:
                                pass
                            try:
                                self._apply_stage_context()
                            except Exception:
                                pass
                            # Inform the user that this capture failed
                            try:
                                if _QtW is not None:
                                    _QtW.QMessageBox.warning(
                                        self,
                                        "Discrete Temp Capture Failed",
                                        "No stable data window was captured for this stage.\n\n"
                                        "Please redo this measurement before continuing.",
                                    )
                            except Exception:
                                pass
                            # Do not treat this as a completed capture; stay in this stage
                            return

                        # Play capture sound (if configured)
                        try:
                            self._play_capture_sound()
                        except Exception:
                            pass

                        # Progress update
                        completed = sum(1 for g in stage.results.values() if g.fz_mean_n is not None)
                        total = stage.total_cells
                        stage_text = f"Stage {stage.index}: {stage.name} @ {stage.location}"
                        self.controls.live_testing_panel.set_stage_progress(stage_text, completed, total)

                        # Temperature Test: auto cycle between stages after two cells per "turn"
                        is_temp = bool(getattr(self._live_session, "is_temp_test", False))
                        if is_temp and not is_discrete:
                            # Count cells completed in this stage's current turn
                            try:
                                self._temp_cells_this_turn = int(getattr(self, "_temp_cells_this_turn", 0)) + 1
                            except Exception:
                                self._temp_cells_this_turn = 1
                            # If all cells across both stages are done, end the session automatically
                            try:
                                if self._temp_all_cells_done():
                                    self._submit_results_and_end()
                                    return
                            except Exception:
                                pass
                            # After two cells in this stage, try switching to the other stage
                            try:
                                if self._temp_cells_this_turn >= 2:
                                    if self._temp_switch_stage_if_possible():
                                        self._temp_cells_this_turn = 0
                            except Exception:
                                pass

                        # Enable Next Stage button when all cells are done (normal, non-temp mode only)
                        if not is_discrete and not bool(getattr(self._live_session, "is_temp_test", False)):
                            try:
                                self.controls.live_testing_panel.btn_next.setEnabled(True)
                            except Exception:
                                pass

                        # Reset window and active cell to allow next capture
                        self._recent_samples.clear()
                        self._active_cell = None
                        self.canvas_left.set_live_active_cell(None, None)
                        self.canvas_right.set_live_active_cell(None, None)
                        self.controls.live_testing_panel.set_debug_status("Captured. Move to next cell…")

                        # Handle stage completion
                        if completed >= total:
                            if is_discrete:
                                self._on_discrete_stage_completed()
                            else:
                                is_temp = bool(getattr(self._live_session, "is_temp_test", False))
                                if is_temp:
                                    # For Temperature Test, if the other stage has remaining cells, schedule a switch
                                    try:
                                        self._temp_switch_stage_if_possible()
                                    except Exception:
                                        pass
                                else:
                                    # Normal mode: enable manual advance and adjust label
                                    try:
                                        self.controls.live_testing_panel.btn_next.setEnabled(True)
                                        # Change button label to Finish on the last stage
                                        if self._live_stage_idx + 1 >= len(self._live_session.stages):
                                            self.controls.live_testing_panel.set_next_stage_label("Finish")
                                    except Exception:
                                        pass
                except Exception:
                    pass


    # --- Automated Tare Guidance -------------------------------------------------
    def _open_tare_dialog(self) -> None:
        try:
            if self._tare_dialog is None:
                self._tare_dialog = TarePromptDialog(self)
                # If the user cancels, defer next attempt by a short grace period
                def _on_reject() -> None:
                    self._tare_active = False
                    self._tare_countdown_remaining_s = 0
                    self._tare_last_tick_s = None
                    # Push next due out a bit so we don't instantly re-open
                    try:
                        self._next_tare_due_ms = int(QtCore.QDateTime.currentMSecsSinceEpoch()) + 30_000
                    except Exception:
                        self._next_tare_due_ms = None
                self._tare_dialog.rejected.connect(_on_reject)
            # Ensure modal but non-blocking
            self._tare_dialog.setModal(True)
            self._tare_dialog.show()
            self._tare_dialog.raise_()
            self._tare_dialog.activateWindow()
        except Exception:
            pass

    def _close_tare_dialog(self) -> None:
        try:
            if self._tare_dialog is not None:
                self._tare_dialog.hide()
        except Exception:
            pass

    def _auto_tare(self) -> None:
        # Emit existing tare signal path; controller taring uses tareAll
        try:
            gid = ""
            try:
                # Optional: use configured group id if provided
                gid = self.controls.group_edit.text().strip()
            except Exception:
                gid = ""
            # Discrete temp: special handling for initial tare (enable bypass, wait 2s, then tare)
            if self._live_session is not None and bool(getattr(self._live_session, "is_discrete_temp", False)):
                # Initial discrete tare: discrete_ready_for_data is still False
                if not bool(getattr(self, "_discrete_ready_for_data", False)):
                    try:
                        if callable(getattr(self, "_on_set_model_bypass_cb", None)):
                            self._on_set_model_bypass_cb(True)
                            self._should_revert_bypass = True
                            self._log("discrete_temp: setModelBypass(true) 2s before initial tare")
                    except Exception:
                        pass
                    # Delay actual tare by 2 seconds to let bypass settle
                    def _do_delayed_tare() -> None:
                        try:
                            self.controls.tare_requested.emit(gid)
                            self._log("auto_tare(discrete, delayed): tare_requested emitted")
                        except Exception:
                            pass
                    try:
                        QtCore.QTimer.singleShot(2000, _do_delayed_tare)
                    except Exception:
                        # Fallback: tare immediately if timer fails; discrete
                        # completion will be handled by the wrapped tare handler.
                        self.controls.tare_requested.emit(gid)
                        self._log("auto_tare(discrete, fallback): tare_requested emitted")
                    return
                # Subsequent discrete tares: immediate tare; discrete completion
                # will be handled by the wrapped tare handler if pending.
                self.controls.tare_requested.emit(gid)
                self._log("auto_tare(discrete): tare_requested emitted")
                return
            # Normal (non-discrete) tare
            self.controls.tare_requested.emit(gid)
            self._log("auto_tare: tare_requested emitted")
        except Exception:
            pass

    def _maybe_run_tare_guidance(self, fz_n: float, t_ms: int, is_visible: bool) -> None:
        # Only in live session and single-device mode
        if self._live_session is None:
            return
        try:
            is_discrete = bool(getattr(self._live_session, "is_discrete_temp", False))
            is_temp = bool(getattr(self._live_session, "is_temp_test", False))
            # During Temperature Test warmup, do not show or update tare guidance yet
            if is_temp and bool(getattr(self, "_temp_warmup_active", False)):
                return
            # Initialize schedule if needed (normal mode only; temp/discrete use explicit flows)
            if not is_discrete and not is_temp and self._next_tare_due_ms is None:
                self._next_tare_due_ms = int(t_ms) + int(getattr(config, "TARE_INTERVAL_S", 90)) * 1000

            # Update dialog if active
            if self._tare_active:
                try:
                    if self._tare_dialog is not None:
                        self._tare_dialog.set_force(float(fz_n))
                except Exception:
                    pass
                # Use a higher step-off threshold during discrete temp sessions so
                # the countdown can start once Fz is comfortably low, without
                # affecting normal live testing behavior.
                base_threshold = float(getattr(config, "TARE_STEP_OFF_THRESHOLD_N", 30.0))
                threshold = 100.0 if is_discrete else base_threshold
                countdown_seed = int(getattr(config, "TARE_COUNTDOWN_S", 15))
                below = abs(float(fz_n)) < threshold
                now_s = int(int(t_ms) / 1000)
                if below:
                    # Start or decrement countdown
                    if self._tare_countdown_remaining_s <= 0:
                        self._tare_countdown_remaining_s = countdown_seed
                        self._tare_last_tick_s = now_s
                    else:
                        # Decrement on whole-second ticks
                        if self._tare_last_tick_s is None or now_s > int(self._tare_last_tick_s):
                            delta = now_s - int(self._tare_last_tick_s or now_s)
                            if delta > 0:
                                self._tare_countdown_remaining_s = max(0, int(self._tare_countdown_remaining_s) - int(delta))
                                self._tare_last_tick_s = now_s
                    try:
                        if self._tare_dialog is not None:
                            self._tare_dialog.set_countdown(int(self._tare_countdown_remaining_s))
                    except Exception:
                        pass
                    # Completed countdown -> perform tare
                    if self._tare_countdown_remaining_s <= 0:
                        self._auto_tare()
                        # Schedule next due from current time
                        self._next_tare_due_ms = int(t_ms) + int(getattr(config, "TARE_INTERVAL_S", 90)) * 1000
                        # Close dialog and reset state
                        self._tare_active = False
                        self._tare_countdown_remaining_s = 0
                        self._tare_last_tick_s = None
                        self._close_tare_dialog()
                else:
                    # Above threshold — reset countdown but keep dialog open
                    self._tare_countdown_remaining_s = countdown_seed
                    self._tare_last_tick_s = now_s
                    try:
                        if self._tare_dialog is not None:
                            self._tare_dialog.set_countdown(int(self._tare_countdown_remaining_s))
                    except Exception:
                        pass
                return

            # Not active: in discrete mode we do not schedule periodic tares here
            if is_discrete:
                return

            # Normal mode: check if due and safe to show (not mid-stabilization)
            if self._next_tare_due_ms is not None and int(t_ms) >= int(self._next_tare_due_ms):
                # Only when no active cell (avoid interrupting stabilization window)
                if self._active_cell is None:
                    self._tare_active = True
                    self._tare_countdown_remaining_s = int(getattr(config, "TARE_COUNTDOWN_S", 15))
                    self._tare_last_tick_s = int(int(t_ms) / 1000)
                    self._open_tare_dialog()
                    # Initialize dialog fields
                    try:
                        if self._tare_dialog is not None:
                            self._tare_dialog.set_force(float(fz_n))
                            self._tare_dialog.set_countdown(int(self._tare_countdown_remaining_s))
                    except Exception:
                        pass
                # else: keep due time; it will fire as soon as _active_cell clears
        except Exception:
            pass
