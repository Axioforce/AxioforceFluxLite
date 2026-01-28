from __future__ import annotations

import copy
import time
from typing import Dict, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from .. import config
from ..app_services.live_measurement_engine import LiveMeasurementEngine
from ..app_services.live_session_gate import LiveSessionGate
from ..app_services.live_test_capture import CaptureContext, TemperatureLiveCaptureManager
from ..app_services.temperature_post_correction import apply_post_correction_to_run_data, compute_delta_t_f
from ..domain.models import TestResult
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
from .widgets.live_cell_details import LiveCellDetailsPanel
from .dialogs.warmup_prompt import WarmupPromptDialog
from .dialogs.tare_prompt import TarePromptDialog
from .dialogs.stage_switch_prompt import StageSwitchPromptDialog


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
        self._live_test_start_enabled_last: Optional[bool] = None

        # Live testing measurement engine (arming -> stability -> capture)
        self._live_meas = LiveMeasurementEngine()
        self._live_meas_status_last: str = ""
        self._live_status_bar_last_text: str = ""
        self._live_status_bar_last_pct: int = -1
        self._live_status_bar_last_mode: str = "idle"
        self._live_meas_active_cell_last: tuple[int, int] | None = None
        # Live session gating: Warmup -> Off-plate tare -> Active session
        self._live_gate = LiveSessionGate()
        self._live_gate_phase_last: str = "inactive"
        self._warmup_dialog: WarmupPromptDialog | None = None
        self._tare_dialog: TarePromptDialog | None = None
        # Temperature live-testing CSV/raw capture lifecycle (backend-driven)
        self._temp_live_capture = TemperatureLiveCaptureManager(self.controller.hardware)
        self._temp_live_capture_ctx: CaptureContext | None = None
        # Temperature test stage switch dialog
        self._stage_switch_dialog: StageSwitchPromptDialog | None = None
        self._stage_switch_pending: bool = False
        self._stage_switch_target_idx: int = -1
        # Periodic auto-tare (every 90 seconds after initial tare)
        self._periodic_tare_interval_ms: int = 90_000  # 90 seconds
        self._periodic_tare_last_ms: int = 0
        self._periodic_tare_pending: bool = False
        self._periodic_tare_countdown_start_ms: int = 0
        self._periodic_tare_dialog: TarePromptDialog | None = None
        self._periodic_tare_due: bool = False  # Timer expired but waiting for measurement
        self._periodic_tare_waiting_cell: tuple[int, int] | None = None  # Cell we're waiting on
        # Some backends send missing/stale timestamps; maintain a monotonic stream clock for countdowns.
        self._stream_time_last_ms: int = 0

        # Legacy Bridge (kept for compatibility)
        self.bridge = UiBridge()

        # UI Setup + wiring
        self._setup_ui()
        self._connect_signals()

        # Start Controller (triggers autoconnect)
        self.controller.start()

    # --- Live Testing logging / enablement helpers ---
    def _lt_log(self, msg: str) -> None:
        """Lightweight live-testing logging (stdout)."""
        try:
            ts = time.strftime("%H:%M:%S")
            print(f"[LiveTesting {ts}] {msg}")
        except Exception:
            pass

    def _is_device_streaming(self, device_id: str) -> bool:
        """Use the same active-device pathway as the Config green check."""
        did = (device_id or "").strip()
        if not did:
            return False
        try:
            # ControlPanel.update_active_devices uses substring matching; mirror it here.
            return any(did in active_id or active_id in did for active_id in (self._active_device_ids or set()))
        except Exception:
            return False

    def _has_active_model(self) -> bool:
        """True if a non-empty, non-placeholder active model is displayed."""
        try:
            live_panel = self.controls.live_testing_panel
            text = (live_panel.lbl_current_model.text() or "").strip()
        except Exception:
            text = ""
        t = text.lower()
        if not text:
            return False
        if text in ("—",):
            return False
        if "loading" in t:
            return False
        if "no active model" in t:
            return False
        return True

    def _live_session_active(self) -> bool:
        """True if a live-test session is currently active in the testing service."""
        try:
            svc = getattr(self.controller, "testing", None)
            sess = getattr(svc, "current_session", None)
            return bool(sess)
        except Exception:
            return False

    def _update_live_test_start_enabled(self, reason: str = "") -> None:
        """
        Enable Start Session only when:
        - a plate is selected
        - that plate is actively streaming (same pathway as Config green check)
        - an active model is present for that plate
        - no live-test session is currently active
        """
        try:
            live_panel = self.controls.live_testing_panel
        except Exception:
            return

        selected_id = (self.state.selected_device_id or "").strip()
        streaming = self._is_device_streaming(selected_id)
        has_model = self._has_active_model()
        session_active = self._live_session_active()
        enabled = bool(selected_id and streaming and has_model and not session_active)

        try:
            live_panel.btn_start.setEnabled(enabled)
        except Exception:
            pass

        if self._live_test_start_enabled_last is None or self._live_test_start_enabled_last != enabled:
            self._live_test_start_enabled_last = enabled
            self._lt_log(
                f"Start Session enabled -> {enabled}"
                f"{' (' + reason + ')' if reason else ''}; "
                f"selected_id={selected_id or '∅'}, "
                f"streaming={streaming}, active_model={has_model}, session_active={session_active}"
            )

    def shutdown(self) -> None:
        """Cleanup and shutdown services."""
        try:
            self.controller.shutdown()
        except Exception:
            pass

    def _close_gate_dialogs(self) -> None:
        """Close warmup/tare dialogs without ending the session."""
        try:
            if self._warmup_dialog is not None:
                try:
                    self._warmup_dialog.rejected.disconnect()
                except Exception:
                    pass
                self._warmup_dialog.close()
        except Exception:
            pass
        try:
            if self._tare_dialog is not None:
                try:
                    self._tare_dialog.rejected.disconnect()
                except Exception:
                    pass
                self._tare_dialog.close()
        except Exception:
            pass
        self._warmup_dialog = None
        self._tare_dialog = None

    def _safe_close_gate_dialog(self, dlg: QtWidgets.QDialog | None) -> None:
        """Close a single gate dialog, ensuring it can't end the session."""
        if dlg is None:
            return
        try:
            try:
                dlg.rejected.disconnect()
            except Exception:
                pass
            dlg.close()
        except Exception:
            pass

    def _reset_live_gate(self, reason: str = "") -> None:
        """Reset warmup/tare gating state."""
        self._close_gate_dialogs()
        try:
            self._live_gate.reset()
        except Exception:
            pass
        self._live_gate_phase_last = "inactive"
        self._stream_time_last_ms = 0
        if reason:
            try:
                self._lt_log(f"gate_reset: {reason}")
            except Exception:
                pass

    def _reset_live_measurement_engine(self, reason: str = "") -> None:
        """Reset only arming/stability/capture state (does NOT touch warmup/tare gate)."""
        try:
            self._live_meas.reset()
        except Exception:
            pass
        self._live_meas_status_last = ""
        self._set_live_status_bar_state(mode="idle", text="", pct=None)
        self._live_meas_active_cell_last = None
        try:
            self.canvas_left.set_live_active_cell(None, None)
            self.canvas_right.set_live_active_cell(None, None)
        except Exception:
            pass
        try:
            # We use the bottom status bar; keep overlay status empty.
            self.canvas_left.set_live_status(None)
        except Exception:
            pass
        if reason:
            try:
                self._lt_log(f"measurement_reset: {reason}")
            except Exception:
                pass

    def _set_live_status_bar_state(self, *, mode: str, text: str, pct: int | None) -> None:
        """
        Update the bottom live-testing status bar.

        mode: idle|arming|measuring
        pct: 0..100 or None (hidden)
        """
        m = str(mode or "idle").strip().lower()
        t = str(text or "")
        p = None if pct is None else int(max(0, min(100, int(pct))))
        if m == self._live_status_bar_last_mode and t == self._live_status_bar_last_text and (p if p is not None else -1) == self._live_status_bar_last_pct:
            return
        self._live_status_bar_last_mode = m
        self._live_status_bar_last_text = t
        self._live_status_bar_last_pct = (p if p is not None else -1)

        # Colors: match active-cell highlight for arming, use yellow for measuring.
        arming_hex = "#00C8FF"
        measuring_hex = "#FFD24A"
        idle_hex = "#BBB"
        color = idle_hex
        if m == "arming":
            color = arming_hex
        elif m == "measuring":
            color = measuring_hex

        show = bool(m in ("arming", "measuring") and t)
        try:
            if hasattr(self, "lbl_live_status_text") and self.lbl_live_status_text is not None:
                self.lbl_live_status_text.setText(t)
                self.lbl_live_status_text.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: 600; background: transparent;")
        except Exception:
            pass
        try:
            if hasattr(self, "lbl_live_status_pct") and self.lbl_live_status_pct is not None:
                self.lbl_live_status_pct.setText(f"{p}%" if (p is not None and show) else "")
                self.lbl_live_status_pct.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: 700; background: transparent;")
        except Exception:
            pass
        try:
            if hasattr(self, "live_status_bar") and self.live_status_bar is not None:
                # Always present to avoid window resize. Contents are blank when idle.
                self.live_status_bar.setVisible(True)
        except Exception:
            pass

    def _setup_ui(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        self.canvas_left = WorldCanvas(self.state, backend_address_provider=self.controller.hardware.backend_http_address)
        self.canvas_right = WorldCanvas(self.state, backend_address_provider=self.controller.hardware.backend_http_address)
        self.canvas = self.canvas_left  # Default active canvas

        # Plate View wrappers so we can host a pop-out cell-details panel.
        self._cell_details_left = LiveCellDetailsPanel(self)
        self._cell_details_right = LiveCellDetailsPanel(self)
        self._cell_details_left.reset_requested.connect(self._on_reset_cell_requested)
        self._cell_details_right.reset_requested.connect(self._on_reset_cell_requested)
        self._cell_details_left.reset_all_fail_requested.connect(self._on_reset_all_fail_requested)
        self._cell_details_right.reset_all_fail_requested.connect(self._on_reset_all_fail_requested)

        try:
            self._cell_details_left.setFixedWidth(260)
            self._cell_details_right.setFixedWidth(260)
            # Border + top padding for a cleaner card look
            style = (
                "QGroupBox {"
                "  background: #1A1A1A;"
                "  border: 1px solid #2A2A2A;"
                "  border-radius: 8px;"
                "  margin-top: 10px;"
                "  padding-top: 10px;"
                "}"
                "QGroupBox::title {"
                "  subcontrol-origin: margin;"
                "  subcontrol-position: top left;"
                "  left: 10px;"
                "  padding: 0 6px;"
                "  color: #BDBDBD;"
                "}"
            )
            self._cell_details_left.setStyleSheet(style)
            self._cell_details_right.setStyleSheet(style)
        except Exception:
            pass

        # Collapsed by default
        self._cell_details_left.setVisible(False)
        self._cell_details_right.setVisible(False)
        self._cell_details_wrap_left = QtWidgets.QWidget()
        self._cell_details_wrap_right = QtWidgets.QWidget()
        self._cell_details_wrap_left.setContentsMargins(0, 0, 0, 0)
        self._cell_details_wrap_right.setContentsMargins(0, 0, 0, 0)
        
        wl = QtWidgets.QVBoxLayout(self._cell_details_wrap_left)
        wr = QtWidgets.QVBoxLayout(self._cell_details_wrap_right)
        wl.setContentsMargins(0, 0, 0, 0)
        wr.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(self._cell_details_left)
        wr.addWidget(self._cell_details_right)
        wl.addStretch(1)
        wr.addStretch(1)
        # Start collapsed so it "pops out" on click.
        try:
            self._cell_details_wrap_left.setFixedWidth(0)
            self._cell_details_wrap_right.setFixedWidth(0)
        except Exception:
            pass

        plate_left = QtWidgets.QWidget()
        pl = QtWidgets.QHBoxLayout(plate_left)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(10)
        pl.addWidget(self.canvas_left, 1)
        pl.addWidget(self._cell_details_wrap_left, 0)

        plate_right = QtWidgets.QWidget()
        pr = QtWidgets.QHBoxLayout(plate_right)
        pr.setContentsMargins(0, 0, 0, 0)
        pr.setSpacing(10)
        pr.addWidget(self.canvas_right, 1)
        pr.addWidget(self._cell_details_wrap_right, 0)

        # Control Panel (Bottom)
        self.controls = ControlPanel(self.state, self.controller)

        self.top_tabs_left = QtWidgets.QTabWidget()
        self.top_tabs_right = QtWidgets.QTabWidget()

        # Left Tabs
        self.top_tabs_left.addTab(plate_left, "Plate View")

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
        self.top_tabs_right.addTab(plate_right, "Plate View")
        self.pane_switcher.register_tab(self.top_tabs_right, plate_right, "plate_view_right")

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

        # Live-testing status bar (bottom). Keep it minimal and non-intrusive.
        status_wrap = QtWidgets.QFrame()
        try:
            status_wrap.setObjectName("live_status_bar")
        except Exception:
            pass
        self.live_status_bar = status_wrap
        try:
            status_wrap.setFrameShape(QtWidgets.QFrame.NoFrame)
            status_wrap.setFixedHeight(22)
        except Exception:
            pass
        status_layout = QtWidgets.QHBoxLayout(status_wrap)
        status_layout.setContentsMargins(10, 4, 10, 4)
        status_layout.setSpacing(8)
        self.lbl_live_status_text = QtWidgets.QLabel("")
        self.lbl_live_status_pct = QtWidgets.QLabel("")
        try:
            self.lbl_live_status_pct.setFixedWidth(48)
        except Exception:
            pass
        status_layout.addWidget(self.lbl_live_status_text, 0)
        status_layout.addWidget(self.lbl_live_status_pct, 0)
        status_layout.addStretch(1)
        outer.addWidget(status_wrap, 0)

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
        # When Live Testing tab becomes visible, refresh gating state
        try:
            self.controls.live_testing_tab_selected.connect(lambda: self._update_live_test_start_enabled("live_tab_selected"))
        except Exception:
            pass

        # Live Testing Signals - show grid on both canvases
        self.controller.live_test.view_grid_configured.connect(self.canvas_left.show_live_grid)
        self.controller.live_test.view_grid_configured.connect(self.canvas_right.show_live_grid)
        self.controller.live_test.view_session_ended.connect(self.canvas_left.hide_live_grid)
        self.controller.live_test.view_session_ended.connect(self.canvas_right.hide_live_grid)
        self.controller.live_test.view_cell_updated.connect(self._on_live_cell_updated)
        # Reset measurement engine when sessions/stages change
        try:
            self.controller.live_test.view_session_started.connect(self._on_live_session_started)
            self.controller.live_test.view_session_ended.connect(self._on_live_session_ended)
            # Stage changes should NOT close gating dialogs; only reset arming/stability.
            self.controller.live_test.view_stage_changed.connect(lambda _i, _st: self._reset_live_measurement_engine("stage_changed"))
            self.controller.live_test.view_stage_changed.connect(lambda _i, _st: self._update_live_stage_nav("stage_changed"))
            self.controller.live_test.view_stage_changed.connect(lambda i, st: self._render_live_stage_grid(int(i), st))
            self.controller.live_test.view_session_ended.connect(lambda: self._update_live_stage_nav("session_ended"))
        except Exception:
            pass

        # User interaction: clicks on the live grid overlay (either canvas)
        self.canvas_left.live_cell_clicked.connect(self._on_live_cell_clicked)
        self.canvas_right.live_cell_clicked.connect(self._on_live_cell_clicked)
        try:
            self.canvas_left.live_background_clicked.connect(lambda: self._hide_cell_details("background_clicked"))
            self.canvas_right.live_background_clicked.connect(lambda: self._hide_cell_details("background_clicked"))
        except Exception:
            pass

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
            # Rotation changes alter COP->cell mapping; reset arming/stability state.
            self.canvas_left.rotation_changed.connect(lambda _k: self._reset_live_measurement_engine("rotation_changed"))
            # Rotation changes must also re-map already-painted cells to the new orientation.
            self.canvas_left.rotation_changed.connect(lambda _k: self._render_current_live_stage_grid("rotation_changed"))
        except Exception:
            pass

        # Discrete Temp: wire test selection to Temp Plot/Slopes
        live_panel = self.controls.live_testing_panel
        live_panel.discrete_test_selected.connect(self.temp_plot_tab.set_test_path)
        live_panel.discrete_test_selected.connect(self._on_discrete_test_selected)  # Switch tabs on selection

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

        # Model Management Signals
        model_svc = self.controller.models
        model_svc.metadata_received.connect(self._on_model_metadata_received)
        model_svc.metadata_error.connect(self._on_model_metadata_error)
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
                    # Some streams omit time or send stale timestamps; fall back to a monotonic local clock.
                    if t_ms <= 0:
                        try:
                            t_ms = int(time.time() * 1000)
                        except Exception:
                            t_ms = 0
                    if t_ms <= int(getattr(self, "_stream_time_last_ms", 0) or 0):
                        try:
                            t_ms = int(time.time() * 1000)
                        except Exception:
                            pass
                    self._stream_time_last_ms = int(t_ms or 0)

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

                        # If tare dialog is showing, keep the live force label updated
                        try:
                            if self._tare_dialog is not None:
                                self._tare_dialog.set_force(float(abs(fz)))
                        except Exception:
                            pass

                        # If stage switch dialog is showing, update force and check threshold
                        try:
                            if self._stage_switch_pending and self._stage_switch_dialog is not None:
                                self._update_stage_switch_dialog_force(float(abs(fz)))
                        except Exception:
                            pass

                        # Live testing warmup/tare gating (must complete before measurement)
                        try:
                            self._process_live_session_gate(t_ms=int(t_ms), fz_abs_n=float(abs(fz)))
                        except Exception:
                            pass

                        # Check periodic tare (every 90 seconds after initial tare)
                        try:
                            self._check_periodic_tare(t_ms=int(t_ms), fz_abs_n=float(abs(fz)))
                        except Exception:
                            pass

                        # Live testing measurement engine (arming -> stability -> capture)
                        if self._live_gate.is_active():
                            try:
                                self._process_live_measurement(
                                    t_ms=t_ms,
                                    cop_x_m=cop_x,
                                    cop_y_m=cop_y,
                                    fz_n=fz,
                                    is_visible=is_visible,
                                )
                            except Exception:
                                pass

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

    def _on_live_session_started(self, _session) -> None:
        """Begin warmup + off-plate tare gating for the new session."""
        self._reset_live_gate("session_started")
        self._reset_live_measurement_engine("session_started")
        # Reset temperature test auto-switch counter
        self._temp_test_cells_since_switch = 0
        # Lock session controls while session is active
        try:
            self.controls.live_testing_panel.set_session_controls_locked(True)
        except Exception:
            pass
        # Temperature live-testing mode: optionally start backend capture immediately.
        try:
            sess = getattr(self.controller.testing, "current_session", None)
            if sess and bool(getattr(sess, "is_temp_test", False)) and not bool(getattr(sess, "is_discrete_temp", False)):
                capture_enabled = False
                save_dir = ""
                try:
                    capture_enabled = bool(self.controls.live_testing_panel._controls_box.is_capture_enabled())
                    save_dir = str(self.controls.live_testing_panel._controls_box.get_save_directory() or "").strip()
                except Exception:
                    capture_enabled = False
                    save_dir = ""
                if capture_enabled:
                    try:
                        gid_fallback = str(self.controls.group_edit.text() or "").strip()
                    except Exception:
                        gid_fallback = ""
                    self._temp_live_capture_ctx = self._temp_live_capture.start(
                        device_id=str(getattr(sess, "device_id", "") or ""),
                        save_dir=save_dir,
                        group_id_fallback=gid_fallback,
                    )
        except Exception:
            pass
        # Initialize the Testing Guide stage tracker (Location A/B overview)
        try:
            sess = getattr(self.controller.testing, "current_session", None)
            if sess and not bool(getattr(sess, "is_discrete_temp", False)):
                # Set Testing Guide mode based on session type
                if bool(getattr(sess, "is_temp_test", False)):
                    self.controls.live_testing_panel._guide_box.set_mode("temperature_test")
                else:
                    self.controls.live_testing_panel._guide_box.set_mode("normal")
                total = int(getattr(sess, "grid_rows", 0) or 0) * int(getattr(sess, "grid_cols", 0) or 0)
                self.controls.live_testing_panel.set_stage_summary(
                    getattr(sess, "stages", []) or [],
                    grid_total_cells=total if total > 0 else None,
                    current_stage_index=int(getattr(self.controller.testing, "current_stage_index", 0) or 0),
                )
        except Exception:
            pass
        self._update_live_stage_nav("session_started")
        try:
            self._live_gate.begin()
            self._live_gate_phase_last = self._live_gate.phase
        except Exception:
            return
        # Warmup dialog shown immediately; countdown begins on first >=50N contact.
        try:
            dlg = WarmupPromptDialog(self, duration_s=20)
            dlg.set_waiting_for_trigger(True)
            dlg.set_remaining(20)
            dlg.rejected.connect(self._on_warmup_dialog_dismissed)
            self._warmup_dialog = dlg
            dlg.show()
        except Exception:
            self._warmup_dialog = None

    def _on_live_session_ended(self) -> None:
        """Clean up when a live test session ends."""
        # Stop temperature-mode backend capture if we started one.
        try:
            ctx = self._temp_live_capture_ctx
            if ctx is not None and str(getattr(ctx, "group_id", "") or "").strip():
                self._temp_live_capture.stop(group_id=str(ctx.group_id))
        except Exception:
            pass
        self._temp_live_capture_ctx = None
        self._reset_live_gate("session_ended")
        self._reset_live_measurement_engine("session_ended")
        # Close stage switch dialog if open
        self._close_stage_switch_dialog()
        # Reset periodic tare state
        self._reset_periodic_tare()
        # Unlock session controls
        try:
            self.controls.live_testing_panel.set_session_controls_locked(False)
        except Exception:
            pass

    def _on_warmup_dialog_dismissed(self) -> None:
        """
        User dismissed warmup dialog (X / Esc).
        Treat warmup as done and proceed to tare.
        """
        try:
            if self._live_gate.phase == "warmup":
                self._lt_log("gate_skip: warmup dismissed -> tare")
                self._live_gate.skip_warmup()
        except Exception:
            pass

    def _on_tare_dialog_dismissed(self) -> None:
        """
        User dismissed tare dialog (X / Esc).
        Treat tare stage as done WITHOUT taring, and allow testing.
        """
        try:
            if self._live_gate.phase == "tare":
                self._lt_log("gate_skip: tare dismissed -> active (no tare)")
                self._live_gate.skip_tare()
                # Clear any status from gating immediately.
                try:
                    self.canvas_left.set_live_status(None)
                except Exception:
                    pass
        except Exception:
            pass

    # --- Periodic Auto-Tare (every 90 seconds) ---

    def _check_periodic_tare(self, t_ms: int, fz_abs_n: float) -> None:
        """Check if periodic tare is due and handle the tare dialog."""
        # Only run periodic tare when gate is active and no other dialogs are pending
        if self._live_gate.phase != "active":
            return
        if self._stage_switch_pending:
            return

        # If periodic tare dialog is already showing, update it
        if self._periodic_tare_pending:
            self._update_periodic_tare_dialog(t_ms, fz_abs_n)
            return

        # Check if we're waiting for a measurement to complete before showing tare
        if self._periodic_tare_due:
            self._check_periodic_tare_waiting(t_ms)
            return

        # Check if interval has elapsed
        elapsed = int(t_ms) - self._periodic_tare_last_ms
        if elapsed >= self._periodic_tare_interval_ms:
            # Check if we're mid-arming or mid-measurement
            phase = str(getattr(self._live_meas, "phase", "idle") or "idle")
            active_cell = getattr(self._live_meas, "active_cell", None)

            if phase in ("arming", "measuring") and active_cell is not None:
                # Don't interrupt - mark as due and wait
                self._periodic_tare_due = True
                self._periodic_tare_waiting_cell = active_cell
                self._lt_log(f"Periodic tare due but waiting (phase={phase}, cell={active_cell})")
            else:
                # Safe to show dialog immediately
                self._show_periodic_tare_dialog(t_ms)

    def _check_periodic_tare_waiting(self, t_ms: int) -> None:
        """Check if we should trigger periodic tare while waiting for measurement."""
        phase = str(getattr(self._live_meas, "phase", "idle") or "idle")
        active_cell = getattr(self._live_meas, "active_cell", None)

        # If measurement completed (phase is idle), show the dialog
        if phase == "idle":
            self._lt_log("Periodic tare: measurement completed, showing dialog")
            self._periodic_tare_due = False
            self._periodic_tare_waiting_cell = None
            self._show_periodic_tare_dialog(t_ms)
            return

        # If the cell changed while we were waiting, force tare immediately
        if self._periodic_tare_waiting_cell is not None and active_cell != self._periodic_tare_waiting_cell:
            self._lt_log(f"Periodic tare: cell changed from {self._periodic_tare_waiting_cell} to {active_cell}, forcing tare")
            self._periodic_tare_due = False
            self._periodic_tare_waiting_cell = None
            self._show_periodic_tare_dialog(t_ms)

    def _show_periodic_tare_dialog(self, t_ms: int) -> None:
        """Show the periodic tare dialog."""
        if self._periodic_tare_pending:
            return

        self._periodic_tare_pending = True
        self._periodic_tare_countdown_start_ms = 0  # Will be set when force < 50N

        try:
            dlg = TarePromptDialog(self)
            dlg.setWindowTitle("Periodic Tare")
            dlg.rejected.connect(self._on_periodic_tare_dialog_dismissed)
            self._periodic_tare_dialog = dlg
            dlg.set_countdown(15)
            dlg.show()
            self._lt_log("Periodic tare dialog shown")
        except Exception:
            self._periodic_tare_pending = False
            self._periodic_tare_dialog = None

    def _update_periodic_tare_dialog(self, t_ms: int, fz_abs_n: float) -> None:
        """Update the periodic tare dialog with force and countdown."""
        if not self._periodic_tare_pending or self._periodic_tare_dialog is None:
            return

        try:
            self._periodic_tare_dialog.set_force(float(fz_abs_n))
        except Exception:
            pass

        # Check if force is below 50N
        if fz_abs_n < 50.0:
            # Start or continue countdown
            if self._periodic_tare_countdown_start_ms == 0:
                self._periodic_tare_countdown_start_ms = int(t_ms)
                self._lt_log("Periodic tare countdown started (force < 50N)")

            elapsed_s = (int(t_ms) - self._periodic_tare_countdown_start_ms) / 1000.0
            remaining_s = max(0, 15 - int(elapsed_s))

            try:
                self._periodic_tare_dialog.set_countdown(remaining_s)
            except Exception:
                pass

            # Check if countdown complete
            if elapsed_s >= 15.0:
                self._complete_periodic_tare(t_ms)
        else:
            # Force went back above 50N, reset countdown
            if self._periodic_tare_countdown_start_ms != 0:
                self._lt_log("Periodic tare countdown reset (force >= 50N)")
            self._periodic_tare_countdown_start_ms = 0
            try:
                self._periodic_tare_dialog.set_countdown(15)
            except Exception:
                pass

    def _complete_periodic_tare(self, t_ms: int) -> None:
        """Complete the periodic tare: issue tare command and close dialog."""
        try:
            self.controller.hardware.tare("")
            self._lt_log("Periodic tare: issued hardware tare")
        except Exception:
            pass

        self._close_periodic_tare_dialog()
        # Reset timer for next periodic tare
        self._periodic_tare_last_ms = int(t_ms)

    def _on_periodic_tare_dialog_dismissed(self) -> None:
        """User dismissed the periodic tare dialog (X / Esc)."""
        self._lt_log("Periodic tare dialog dismissed by user (skipping tare)")
        self._close_periodic_tare_dialog()
        # Reset timer anyway so they get another chance in 90 seconds
        self._periodic_tare_last_ms = self._stream_time_last_ms

    def _close_periodic_tare_dialog(self) -> None:
        """Close the periodic tare dialog and reset state."""
        try:
            if self._periodic_tare_dialog is not None:
                try:
                    self._periodic_tare_dialog.rejected.disconnect()
                except Exception:
                    pass
                self._periodic_tare_dialog.close()
        except Exception:
            pass
        self._periodic_tare_dialog = None
        self._periodic_tare_pending = False
        self._periodic_tare_countdown_start_ms = 0

    def _reset_periodic_tare(self) -> None:
        """Reset periodic tare state (called when session ends)."""
        self._close_periodic_tare_dialog()
        self._periodic_tare_last_ms = 0
        self._periodic_tare_due = False
        self._periodic_tare_waiting_cell = None

    def _process_live_session_gate(self, *, t_ms: int, fz_abs_n: float) -> None:
        """Advance warmup/tare gating using the current force sample."""
        sess = self.controller.testing.current_session
        if not sess:
            return
        if bool(getattr(sess, "is_discrete_temp", False)):
            return
        if self._live_gate.phase == "inactive":
            return

        info = self._live_gate.update(now_ms=int(t_ms), fz_abs_n=float(fz_abs_n))
        phase = str(info.get("phase") or "inactive")

        # Phase transitions drive dialog lifecycle
        if phase != self._live_gate_phase_last:
            self._live_gate_phase_last = phase
            try:
                self._lt_log(f"gate_phase -> {phase}")
            except Exception:
                pass
            # Close warmup dialog when leaving warmup
            if phase != "warmup":
                self._safe_close_gate_dialog(self._warmup_dialog)
                self._warmup_dialog = None
            # Show tare dialog when entering tare
            if phase == "tare":
                try:
                    td = TarePromptDialog(self)
                    td.rejected.connect(self._on_tare_dialog_dismissed)
                    self._tare_dialog = td
                    td.set_force(float(fz_abs_n))
                    td.set_countdown(15)
                    td.show()
                except Exception:
                    self._tare_dialog = None
            # Close tare dialog when entering active
            if phase == "active":
                self._safe_close_gate_dialog(self._tare_dialog)
                self._tare_dialog = None
                # Start periodic tare timer
                self._periodic_tare_last_ms = int(t_ms)
                self._periodic_tare_pending = False
                self._lt_log(f"Periodic tare timer started (interval={self._periodic_tare_interval_ms}ms)")
                # Clear any status from gating (overlay + status bar)
                try:
                    self.canvas_left.set_live_status(None)
                except Exception:
                    pass
                self._set_live_status_bar_state(mode="idle", text="", pct=None)

        # Update dialog contents
        if phase == "warmup":
            try:
                triggered = bool(info.get("warmup_triggered"))
                remaining = info.get("warmup_remaining_s")
                if self._warmup_dialog is not None:
                    self._warmup_dialog.set_waiting_for_trigger(not triggered)
                    if remaining is None:
                        self._warmup_dialog.set_remaining(20)
                    else:
                        self._warmup_dialog.set_remaining(int(remaining))
            except Exception:
                pass
            return

        if phase == "tare":
            try:
                remaining = info.get("tare_remaining_s")
                if self._tare_dialog is not None:
                    self._tare_dialog.set_force(float(fz_abs_n))
                    self._tare_dialog.set_countdown(int(remaining) if remaining is not None else 15)
            except Exception:
                pass

        # Fire tare exactly once when the gate says so
        if bool(info.get("should_tare")):
            try:
                self.controller.hardware.tare("")
                self._lt_log("gate_tare: issued hardware tare")
            except Exception:
                pass

    def _process_live_measurement(self, *, t_ms: int, cop_x_m: float, cop_y_m: float, fz_n: float, is_visible: bool) -> None:
        """
        Run arming/stability/capture for the currently-selected device when a live session is active.
        """
        sess = self.controller.testing.current_session
        if not sess:
            return
        # Do not capture measurements while stage switch dialog is showing
        if self._stage_switch_pending:
            return
        # Do not capture measurements while periodic tare dialog is showing
        if self._periodic_tare_pending:
            return
        # Do not change discrete-temp capture behavior yet (it has its own flow).
        if bool(getattr(sess, "is_discrete_temp", False)):
            return
        if self.state.display_mode != "single":
            return

        selected_id = (self.state.selected_device_id or "").strip()
        if not selected_id:
            return
        # Session is tied to a specific device id
        if str(getattr(sess, "device_id", "") or "").strip() != selected_id:
            return

        try:
            stage_idx = int(getattr(self.controller.testing, "current_stage_index", 0))
        except Exception:
            stage_idx = 0
        try:
            stage = sess.stages[stage_idx]
        except Exception:
            return

        dev_type = (self.state.selected_device_type or "").strip() or str(getattr(sess, "model_id", "") or "").strip() or "06"
        rows = int(getattr(sess, "grid_rows", 0) or 0)
        cols = int(getattr(sess, "grid_cols", 0) or 0)
        if rows <= 0 or cols <= 0:
            return

        # COP from backend is in meters (renderer converts m->mm). Convert here too.
        cop_x_mm = float(cop_x_m) * 1000.0
        cop_y_mm = float(cop_y_m) * 1000.0

        try:
            rot_k = int(self.canvas_left.get_rotation_quadrants())
        except Exception:
            rot_k = 0

        def _already_done(r: int, c: int) -> bool:
            try:
                existing = (stage.results or {}).get((int(r), int(c)))
                return bool(existing and getattr(existing, "fz_mean_n", None) is not None)
            except Exception:
                return False

        ev = self._live_meas.process_sample(
            t_ms=int(t_ms),
            cop_x_mm=float(cop_x_mm),
            cop_y_mm=float(cop_y_mm),
            fz_n=float(fz_n),
            is_visible=bool(is_visible),
            device_type=str(dev_type),
            rows=int(rows),
            cols=int(cols),
            rotation_quadrants=int(rot_k),
            is_cell_already_done=_already_done,
        )

        # Active cell highlight + status text (throttled to changes)
        try:
            active = self._live_meas.active_cell
            if active != self._live_meas_active_cell_last:
                self._live_meas_active_cell_last = active
                if active is None:
                    self.canvas_left.set_live_active_cell(None, None)
                    self.canvas_right.set_live_active_cell(None, None)
                else:
                    self.canvas_left.set_live_active_cell(int(active[0]), int(active[1]))
                    self.canvas_right.set_live_active_cell(int(active[0]), int(active[1]))
        except Exception:
            pass

        # Replace verbose overlay assistant with a simple bottom status bar.
        # Only show "Arming..." when we are *actually* arming in an unmeasured cell.
        try:
            phase = str(getattr(self._live_meas, "phase", "idle") or "idle")
            prog = float(getattr(self._live_meas, "progress_01", 0.0) or 0.0)
            pct = int(max(0, min(100, round(100.0 * prog))))
            if phase == "arming":
                self._set_live_status_bar_state(mode="arming", text="Arming...", pct=pct)
            elif phase == "measuring":
                self._set_live_status_bar_state(mode="measuring", text="Taking measurement...", pct=pct)
            else:
                self._set_live_status_bar_state(mode="idle", text="", pct=None)
        except Exception:
            self._set_live_status_bar_state(mode="idle", text="", pct=None)
        # Keep overlay status empty for a cleaner plate view.
        try:
            self.canvas_left.set_live_status(None)
        except Exception:
            pass

        if not ev:
            return

        # Captured: clear the status bar so it doesn't linger.
        self._set_live_status_bar_state(mode="idle", text="", pct=None)

        # Commit captured measurement into the session via TestingService
        res = TestResult(
            row=int(ev.row),
            col=int(ev.col),
            fz_mean_n=float(ev.mean_fz_n),
            cop_x_mm=float(ev.mean_cop_x_mm),
            cop_y_mm=float(ev.mean_cop_y_mm),
        )
        self.controller.testing.record_result(int(stage_idx), int(ev.row), int(ev.col), res)

        # For Temperature Test mode: use stage-specific colors (no pass/fail)
        is_temp_test = bool(getattr(sess, "is_temp_test", False))
        if is_temp_test:
            self._apply_temp_test_cell_color(stage, int(ev.row), int(ev.col))

        # Update progress/Next button using existing LiveTestingPanel helpers
        try:
            completed = 0
            for r in (stage.results or {}).values():
                try:
                    if r is not None and getattr(r, "fz_mean_n", None) is not None:
                        completed += 1
                except Exception:
                    continue
            total = int(getattr(stage, "total_cells", rows * cols) or (rows * cols))
            stage_text = str(getattr(stage, "name", "") or "Stage")
            self.controls.live_testing_panel.set_stage_progress(stage_text, completed, total)
            # Navigation gating handled by `_update_live_stage_nav`.
            self._update_live_stage_nav("cell_captured")
            # Also update the per-stage summary tracker (Location A/B overview)
            try:
                sess2 = getattr(self.controller.testing, "current_session", None)
                if sess2 and not bool(getattr(sess2, "is_discrete_temp", False)):
                    total2 = int(getattr(sess2, "grid_rows", 0) or 0) * int(getattr(sess2, "grid_cols", 0) or 0)
                    self.controls.live_testing_panel.set_stage_summary(
                        getattr(sess2, "stages", []) or [],
                        grid_total_cells=total2 if total2 > 0 else None,
                        current_stage_index=int(stage_idx),
                    )
            except Exception:
                pass

            # Temperature Test mode: auto-switch stages after 2 cells or stage completion
            if is_temp_test:
                self._maybe_auto_switch_temp_test_stage(sess, stage_idx, completed, total)
        except Exception:
            pass

    def _update_live_stage_nav(self, reason: str = "") -> None:
        """
        Normal live testing navigation rule:
        - can move freely within stages 0..2 (Location A)
        - cannot move into stages 3..5 (Location B) until stages 0..2 are complete
        - once unlocked, can move freely across 0..5
        """
        try:
            panel = self.controls.live_testing_panel
        except Exception:
            return

        sess = getattr(self.controller.testing, "current_session", None)
        if not sess or bool(getattr(sess, "is_discrete_temp", False)) or bool(getattr(sess, "is_temp_test", False)):
            # Default behavior for non-standard modes: sequential navigation.
            try:
                idx = int(getattr(self.controller.testing, "current_stage_index", 0) or 0)
            except Exception:
                idx = 0
            try:
                total_stages = len(getattr(sess, "stages", []) or [])
            except Exception:
                total_stages = 0
            panel.set_prev_stage_enabled(bool(idx > 0))
            panel.set_next_stage_enabled(bool(total_stages > 0 and idx < total_stages - 1))
            return

        stages = getattr(sess, "stages", []) or []
        try:
            idx = int(getattr(self.controller.testing, "current_stage_index", 0) or 0)
        except Exception:
            idx = 0

        if len(stages) < 6:
            panel.set_prev_stage_enabled(bool(idx > 0))
            panel.set_next_stage_enabled(bool(idx < max(0, len(stages) - 1)))
            return

        prev_enabled = bool(idx > 0)
        next_enabled = bool(idx < len(stages) - 1)

        panel.set_prev_stage_enabled(prev_enabled)
        panel.set_next_stage_enabled(next_enabled)

    def _apply_temp_test_cell_color(self, stage: object, row: int, col: int) -> None:
        """Apply stage-specific color for Temperature Test mode (no pass/fail)."""
        stage_name = str(getattr(stage, "name", "") or "").lower()
        # Pink for 45 lb stage, Purple for Bodyweight stage
        if "45" in stage_name:
            color = QtGui.QColor(255, 105, 180, 160)  # Pink
        else:
            color = QtGui.QColor(148, 103, 189, 160)  # Purple
        try:
            self.canvas_left.set_live_cell_color(int(row), int(col), color)
            self.canvas_right.set_live_cell_color(int(row), int(col), color)
        except Exception:
            pass

    def _maybe_auto_switch_temp_test_stage(self, sess: object, current_stage_idx: int, completed: int, total: int) -> None:
        """
        Auto-switch stages for Temperature Test mode.
        Show a dialog after 2 cells are captured OR the current stage is complete.
        The actual switch happens when force drops below 50N.
        """
        # Don't trigger if a switch is already pending
        if self._stage_switch_pending:
            return

        stages = getattr(sess, "stages", []) or []
        if len(stages) != 2:
            return  # Only applies to 2-stage temperature test

        # Determine the other stage index
        other_stage_idx = 1 - current_stage_idx

        def _should_switch_to_other() -> bool:
            """Check if the other stage has remaining cells."""
            try:
                other_stage = stages[other_stage_idx]
                other_completed = sum(
                    1 for r in (getattr(other_stage, "results", {}) or {}).values()
                    if r is not None and getattr(r, "fz_mean_n", None) is not None
                )
                other_total = int(getattr(other_stage, "total_cells", 0) or 0)
                return other_completed < other_total
            except Exception:
                return False

        # Check if current stage is complete
        if completed >= total:
            if _should_switch_to_other():
                self._show_stage_switch_dialog(other_stage_idx, stages, "stage_complete")
            return

        # Check how many cells we've captured in current stage since last switch
        if not hasattr(self, "_temp_test_cells_since_switch"):
            self._temp_test_cells_since_switch = 0
        self._temp_test_cells_since_switch += 1

        if self._temp_test_cells_since_switch >= 2:
            if _should_switch_to_other():
                self._show_stage_switch_dialog(other_stage_idx, stages, "2_cells_captured")

    def _show_stage_switch_dialog(self, target_stage_idx: int, stages: list, reason: str = "") -> None:
        """Show the stage switch dialog and wait for force to drop below 50N."""
        if self._stage_switch_pending:
            return

        try:
            target_stage = stages[target_stage_idx]
            target_name = str(getattr(target_stage, "name", "") or "")
            # Make the display name user-friendly
            if "45" in target_name.lower():
                display_name = "45 lb Dumbbell"
            else:
                display_name = "Bodyweight"
        except Exception:
            display_name = "Next Stage"

        self._stage_switch_pending = True
        self._stage_switch_target_idx = target_stage_idx

        try:
            dlg = StageSwitchPromptDialog(self, target_stage=display_name)
            dlg.rejected.connect(self._on_stage_switch_dialog_dismissed)
            dlg.switch_ready.connect(self._on_stage_switch_ready)
            self._stage_switch_dialog = dlg
            dlg.show()
            self._lt_log(f"Stage switch dialog shown: target={display_name} ({reason})")
        except Exception:
            self._stage_switch_pending = False
            self._stage_switch_dialog = None

    def _on_stage_switch_dialog_dismissed(self) -> None:
        """User dismissed the stage switch dialog (X / Esc)."""
        self._stage_switch_pending = False
        self._stage_switch_target_idx = -1
        self._stage_switch_dialog = None
        # Reset the counter so it triggers again after 2 more cells
        self._temp_test_cells_since_switch = 0
        self._lt_log("Stage switch dialog dismissed by user")

    def _on_stage_switch_ready(self) -> None:
        """Force dropped below threshold, perform the actual stage switch."""
        target_idx = self._stage_switch_target_idx
        self._close_stage_switch_dialog()

        if target_idx < 0:
            return

        try:
            current_idx = int(getattr(self.controller.testing, "current_stage_index", 0) or 0)
            if target_idx == current_idx:
                return
            if target_idx > current_idx:
                self.controller.testing.next_stage()
            else:
                self.controller.testing.prev_stage()
            # Reset cells-since-switch counter
            self._temp_test_cells_since_switch = 0
            self._lt_log(f"Auto-switched to stage {target_idx} (force < 50N)")
        except Exception:
            pass

    def _close_stage_switch_dialog(self) -> None:
        """Close the stage switch dialog and reset state."""
        try:
            if self._stage_switch_dialog is not None:
                try:
                    self._stage_switch_dialog.rejected.disconnect()
                except Exception:
                    pass
                try:
                    self._stage_switch_dialog.switch_ready.disconnect()
                except Exception:
                    pass
                self._stage_switch_dialog.close()
        except Exception:
            pass
        self._stage_switch_dialog = None
        self._stage_switch_pending = False
        self._stage_switch_target_idx = -1

    def _update_stage_switch_dialog_force(self, fz_n: float) -> None:
        """Update the stage switch dialog with current force and check threshold."""
        if not self._stage_switch_pending or self._stage_switch_dialog is None:
            return

        try:
            self._stage_switch_dialog.set_force(float(fz_n))
        except Exception:
            pass

        # Check if force dropped below 50N
        try:
            if abs(float(fz_n)) < 50.0:
                self._stage_switch_dialog.signal_ready()
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

        # Apply to both plate views so switching tabs/panes stays consistent.
        try:
            self.canvas_left.set_live_cell_color(row, col, color)
            self.canvas_right.set_live_cell_color(row, col, color)
            if text:
                self.canvas_left.set_live_cell_text(row, col, text)
                self.canvas_right.set_live_cell_text(row, col, text)
        except Exception:
            # Fallback to legacy "active canvas" behavior
            try:
                self.canvas.set_live_cell_color(row, col, color)
                if text:
                    self.canvas.set_live_cell_text(row, col, text)
            except Exception:
                pass

    def _render_live_stage_grid(self, stage_idx: int, stage: object) -> None:
        """When switching stages, redraw grid colors/text for that stage's results."""
        sess = getattr(self.controller.testing, "current_session", None)
        if not sess:
            return
        # Discrete temp has its own grid rendering path.
        if bool(getattr(sess, "is_discrete_temp", False)):
            return

        # Clear existing cell colors/text
        try:
            self.canvas_left.clear_live_colors()
            self.canvas_right.clear_live_colors()
        except Exception:
            pass

        try:
            presenter = getattr(self.controller.live_test, "presenter", None)
        except Exception:
            presenter = None
        if presenter is None:
            return

        # Determine target + tolerance for this stage
        try:
            target_n = float(getattr(stage, "target_n", 0.0) or 0.0)
        except Exception:
            target_n = 0.0
        try:
            st_name = str(getattr(stage, "name", "") or "")
        except Exception:
            st_name = ""
        # Use the same tolerance logic as capture-time coloring (single source of truth).
        tol_n = float(self.controller.live_test.tolerance_for_stage(stage, sess))

        # Apply every recorded result
        try:
            results = getattr(stage, "results", {}) or {}
        except Exception:
            results = {}

        # Temperature Test mode: use stage-specific colors (no pass/fail)
        is_temp_test = bool(getattr(sess, "is_temp_test", False))
        if is_temp_test:
            # Pink for 45 lb stage, Purple for Bodyweight stage
            if "45" in st_name.lower():
                stage_color = QtGui.QColor(255, 105, 180, 160)  # Pink
            else:
                stage_color = QtGui.QColor(148, 103, 189, 160)  # Purple
            for (r, c), res in results.items():
                try:
                    if res is None or getattr(res, "fz_mean_n", None) is None:
                        continue
                    self.canvas_left.set_live_cell_color(int(r), int(c), stage_color)
                    self.canvas_right.set_live_cell_color(int(r), int(c), stage_color)
                except Exception:
                    continue
        else:
            for (r, c), res in results.items():
                try:
                    if res is None or getattr(res, "fz_mean_n", None) is None:
                        continue
                    vm = presenter.compute_live_cell(res, float(target_n), float(tol_n))
                    self.canvas_left.set_live_cell_color(int(r), int(c), vm.color)
                    self.canvas_right.set_live_cell_color(int(r), int(c), vm.color)
                    if getattr(vm, "text", None):
                        self.canvas_left.set_live_cell_text(int(r), int(c), str(vm.text))
                        self.canvas_right.set_live_cell_text(int(r), int(c), str(vm.text))
                except Exception:
                    continue

        # Update the stage progress label to match this stage's actual completion.
        try:
            total = int(getattr(stage, "total_cells", 0) or 0)
        except Exception:
            total = 0
        done = 0
        try:
            for rr in (results or {}).values():
                try:
                    if rr is not None and getattr(rr, "fz_mean_n", None) is not None:
                        done += 1
                except Exception:
                    continue
        except Exception:
            done = 0
        try:
            self.controls.live_testing_panel.set_stage_progress(str(st_name or "Stage"), int(done), int(total))
        except Exception:
            pass

    def _render_current_live_stage_grid(self, reason: str = "") -> None:
        """Repaint current stage grid (e.g., after rotation)."""
        sess = getattr(self.controller.testing, "current_session", None)
        if not sess:
            return
        if bool(getattr(sess, "is_discrete_temp", False)):
            return
        try:
            idx = int(getattr(self.controller.testing, "current_stage_index", 0) or 0)
        except Exception:
            idx = 0
        try:
            stages = getattr(sess, "stages", []) or []
            if 0 <= idx < len(stages):
                self._render_live_stage_grid(int(idx), stages[int(idx)])
        except Exception:
            return

    def _on_live_cell_clicked(self, row: int, col: int) -> None:
        """Bridge canvas cell clicks into the live-test controller."""
        # During standard live testing we capture automatically; clicking is used for inspection/reset UI.
        try:
            self._show_cell_details(int(row), int(col))
        except Exception:
            pass
        # Keep click-to-record behavior for discrete temp only.
        try:
            sess = self.controller.testing.current_session
            if sess and not bool(getattr(sess, "is_discrete_temp", False)):
                return
        except Exception:
            pass
        try:
            self.controller.live_test.handle_cell_click(int(row), int(col), {})
        except Exception:
            pass

    def _show_cell_details(self, row: int, col: int) -> None:
        sess = getattr(self.controller.testing, "current_session", None)
        if not sess:
            self._cell_details_left.clear()
            self._cell_details_right.clear()
            self._cell_details_left.setVisible(False)
            self._cell_details_right.setVisible(False)
            try:
                self._cell_details_wrap_left.setFixedWidth(0)
                self._cell_details_wrap_right.setFixedWidth(0)
            except Exception:
                pass
            return
        if bool(getattr(sess, "is_discrete_temp", False)):
            return
        try:
            stage_idx = int(getattr(self.controller.testing, "current_stage_index", 0) or 0)
        except Exception:
            stage_idx = 0
        stages = getattr(sess, "stages", []) or []
        if not (0 <= stage_idx < len(stages)):
            return
        stage = stages[stage_idx]
        target_n = None
        try:
            target_n = float(getattr(stage, "target_n", None))
        except Exception:
            target_n = None
        measured = None
        try:
            res = (getattr(stage, "results", {}) or {}).get((int(row), int(col)))
            if res is not None and getattr(res, "fz_mean_n", None) is not None:
                measured = float(getattr(res, "fz_mean_n"))
        except Exception:
            measured = None

        # Show on both plate views
        self._cell_details_left.set_cell(stage_idx=stage_idx, row=row, col=col, measured_n=measured, target_n=target_n)
        self._cell_details_right.set_cell(stage_idx=stage_idx, row=row, col=col, measured_n=measured, target_n=target_n)
        self._cell_details_left.setVisible(True)
        self._cell_details_right.setVisible(True)
        try:
            self._cell_details_wrap_left.setFixedWidth(260)
            self._cell_details_wrap_right.setFixedWidth(260)
        except Exception:
            pass
        # Show reset-count badges only while inspector is open (normal live testing)
        try:
            if not bool(getattr(sess, "is_temp_test", False)) and not bool(getattr(sess, "is_discrete_temp", False)):
                self._apply_reset_badges_for_stage(int(stage_idx))
        except Exception:
            pass

    def _hide_cell_details(self, _reason: str = "") -> None:
        try:
            self._clear_reset_badges()
        except Exception:
            pass
        try:
            self._cell_details_left.clear()
            self._cell_details_right.clear()
            self._cell_details_left.setVisible(False)
            self._cell_details_right.setVisible(False)
        except Exception:
            pass
        try:
            self._cell_details_wrap_left.setFixedWidth(0)
            self._cell_details_wrap_right.setFixedWidth(0)
        except Exception:
            pass

    def _clear_reset_badges(self) -> None:
        """Clear all top-right reset badges for the current stage."""
        sess = getattr(self.controller.testing, "current_session", None)
        if not sess:
            return
        stages = getattr(sess, "stages", []) or []
        try:
            idx = int(getattr(self.controller.testing, "current_stage_index", 0) or 0)
        except Exception:
            idx = 0
        if not (0 <= idx < len(stages)):
            return
        stage = stages[idx]
        counts = getattr(stage, "reset_counts", {}) or {}
        for (r, c) in list(counts.keys()):
            try:
                self.canvas_left.set_live_cell_corner_text(int(r), int(c), None)
                self.canvas_right.set_live_cell_corner_text(int(r), int(c), None)
            except Exception:
                continue

    def _apply_reset_badges_for_stage(self, stage_idx: int) -> None:
        """Apply top-right reset badge numbers for this stage."""
        sess = getattr(self.controller.testing, "current_session", None)
        if not sess:
            return
        stages = getattr(sess, "stages", []) or []
        if not (0 <= int(stage_idx) < len(stages)):
            return
        stage = stages[int(stage_idx)]
        counts = getattr(stage, "reset_counts", {}) or {}
        for (r, c), n in counts.items():
            try:
                txt = str(int(n)) if int(n) > 0 else ""
                self.canvas_left.set_live_cell_corner_text(int(r), int(c), txt or None)
                self.canvas_right.set_live_cell_corner_text(int(r), int(c), txt or None)
            except Exception:
                continue

    def _on_reset_cell_requested(self, stage_idx: int, row: int, col: int) -> None:
        """Clear a measured cell for a given stage and redraw."""
        sess = getattr(self.controller.testing, "current_session", None)
        if not sess:
            return
        stages = getattr(sess, "stages", []) or []
        if not (0 <= int(stage_idx) < len(stages)):
            return
        stage = stages[int(stage_idx)]
        # Track reset count for this stage/cell
        try:
            rc = getattr(stage, "reset_counts", None)
            if isinstance(rc, dict):
                key = (int(row), int(col))
                rc[key] = int(rc.get(key, 0) or 0) + 1
        except Exception:
            pass
        try:
            results = getattr(stage, "results", None)
            if isinstance(results, dict):
                results.pop((int(row), int(col)), None)
        except Exception:
            pass

        # Redraw current stage grid (keeps rotation mapping correct)
        try:
            cur = int(getattr(self.controller.testing, "current_stage_index", 0) or 0)
        except Exception:
            cur = 0
        if int(stage_idx) == int(cur):
            try:
                self._render_live_stage_grid(int(stage_idx), stage)
            except Exception:
                pass
        else:
            # If they reset a non-current stage (possible via other modes later), still refresh tracker.
            try:
                self.controls.live_testing_panel.set_stage_summary(
                    getattr(sess, "stages", []) or [],
                    grid_total_cells=int(getattr(sess, "grid_rows", 0) or 0) * int(getattr(sess, "grid_cols", 0) or 0) or None,
                    current_stage_index=int(cur),
                )
            except Exception:
                pass

        # Update the inspector with the cleared state
        self._hide_cell_details("reset_clicked")

    def _on_reset_all_fail_requested(self, stage_idx: int) -> None:
        """Reset all failed cells on this stage."""
        sess = getattr(self.controller.testing, "current_session", None)
        if not sess:
            return
        if bool(getattr(sess, "is_discrete_temp", False)) or bool(getattr(sess, "is_temp_test", False)):
            return
        stages = getattr(sess, "stages", []) or []
        if not (0 <= int(stage_idx) < len(stages)):
            return
        stage = stages[int(stage_idx)]
        results = getattr(stage, "results", {}) or {}

        try:
            tol_n = float(self.controller.live_test.tolerance_for_stage(stage, sess))
        except Exception:
            return
        try:
            target_n = float(getattr(stage, "target_n", 0.0) or 0.0)
        except Exception:
            target_n = 0.0

        fail_cut = float(config.COLOR_BIN_MULTIPLIERS["light_green"])
        to_reset: list[tuple[int, int]] = []
        for (r, c), res in list(results.items()):
            try:
                mean_n = getattr(res, "fz_mean_n", None)
                if mean_n is None:
                    continue
                if float(tol_n) <= 0:
                    continue
                err_ratio = abs(float(mean_n) - float(target_n)) / float(tol_n)
                if err_ratio > fail_cut:
                    to_reset.append((int(r), int(c)))
            except Exception:
                continue

        if not to_reset:
            self._hide_cell_details("reset_all_fail_noop")
            return

        # Increment reset counts + clear results
        try:
            rc = getattr(stage, "reset_counts", None)
            if isinstance(rc, dict):
                for r, c in to_reset:
                    key = (int(r), int(c))
                    rc[key] = int(rc.get(key, 0) or 0) + 1
        except Exception:
            pass
        for r, c in to_reset:
            try:
                results.pop((int(r), int(c)), None)
            except Exception:
                continue

        # Redraw if we're on this stage
        try:
            cur = int(getattr(self.controller.testing, "current_stage_index", 0) or 0)
        except Exception:
            cur = 0
        if int(stage_idx) == int(cur):
            try:
                self._render_live_stage_grid(int(stage_idx), stage)
            except Exception:
                pass
        self._hide_cell_details("reset_all_fail_clicked")

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
        Only auto-select when transitioning from 0 streaming devices to 1+ streaming devices.
        Once a device is selected, stick with it until it stops streaming entirely.
        """
        try:
            prev_active = getattr(self, "_active_device_ids", set())
            self._active_device_ids = set(str(x) for x in (active_device_ids or set()) if str(x).strip())
            # Gate Live Testing start on active streaming set changes (same source as Config green check).
            self._update_live_test_start_enabled("active_devices_updated")
            active = sorted(self._active_device_ids)
            if not active:
                if not self._connected_device_ids:
                    self._clear_device_views()
                return

            selected = str(self.state.selected_device_id or "").strip()

            # Only auto-select when:
            # 1. No device is currently selected, AND
            # 2. We just transitioned from 0 active devices to 1+ active devices
            had_no_active_before = len(prev_active) == 0
            should_select = (not selected) and had_no_active_before
            if not should_select:
                return

            # Select the first device (sorted alphabetically) and stick with it
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
            self._update_live_test_start_enabled("clear_device_views")
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
            self._lt_log(f"Selection changed: device_id={device_id or '∅'} type={device_type or '∅'} name={device_name or '∅'}")
            self._update_live_test_start_enabled("config_changed")
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
            try:
                self._lt_log(f"Model metadata received: count={len(models or [])}")
            except Exception:
                pass
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
                self._lt_log(f"Active model detected: {active_model}")
                live_panel.set_current_model(str(active_model))
                live_panel.set_session_model_id(str(active_model))
            else:
                self._lt_log("No active model found")
                live_panel.set_current_model("No active model")

            live_panel.set_model_status(None)  # Clear any loading status
            self._update_live_test_start_enabled("model_metadata_received")
        except Exception as e:
            print(f"[FluxLitePage] Model metadata error: {e}")

    def _on_model_metadata_error(self, error_msg: str) -> None:
        """Handle model metadata request errors (timeout, socket error, etc.)."""
        try:
            print(f"[FluxLitePage] Model metadata error: {error_msg}")
            live_panel = self.controls.live_testing_panel
            live_panel.set_current_model("Error loading models")
            live_panel.set_model_list([])
            live_panel.set_model_status(error_msg)
            live_panel.set_model_controls_enabled(True)
        except Exception as e:
            print(f"[FluxLitePage] Error handling metadata error: {e}")

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
            self._update_live_test_start_enabled("model_activation_status")

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

