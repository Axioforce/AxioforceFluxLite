from __future__ import annotations

from typing import Callable, Dict, Optional, Tuple, List

from PySide6 import QtCore, QtWidgets

from .. import config
from .bridge import UiBridge
from .state import ViewState
from .widgets.world_canvas import WorldCanvas
from .panels.control_panel import ControlPanel
from .panels.live_testing_panel import LiveTestingPanel
from .dialogs.live_test_setup import LiveTestSetupDialog
from .dialogs.live_test_summary import LiveTestSummaryDialog
from .dialogs.tare_prompt import TarePromptDialog
from .dialogs.model_packager import ModelPackagerDialog
from ..live_testing_model import GRID_BY_MODEL, LiveTestSession, LiveTestStage, GridCellResult, Thresholds
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
        # Live Testing UI will live in bottom control panel
        

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
        # Live Testing UI will live in bottom control panel
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
        self.statusBar().addPermanentWidget(self.status_label)

        self.controls.config_changed.connect(self._on_config_changed)
        self.controls.refresh_devices_requested.connect(self._on_refresh_devices)
        self.controls.live_testing_tab_selected.connect(self._on_live_tab_selected)
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
        # Model management updates
        self.bridge.model_metadata_ready.connect(self._on_model_metadata)
        self.bridge.model_package_status_ready.connect(self._on_model_package_status)
        self.bridge.model_activation_status_ready.connect(self._on_model_activation_status)

        # Live testing wiring (bottom panel)
        try:
            self.controls.live_testing_panel.start_session_requested.connect(self._on_live_start)
            self.controls.live_testing_panel.end_session_requested.connect(self._on_live_end)
            self.controls.live_testing_panel.next_stage_requested.connect(self._on_next_stage)
            self.controls.live_testing_panel.package_model_requested.connect(self._on_package_model_clicked)
            self.controls.live_testing_panel.activate_model_requested.connect(self._on_activate_model)
            self.controls.live_testing_panel.deactivate_model_requested.connect(self._on_deactivate_model)
        except Exception:
            pass

        self._live_session: Optional[LiveTestSession] = None
        self._live_stage_idx: int = 0
        self._active_cell: Optional[Tuple[int, int]] = None
        self._stability_window_ms: int = 2000
        self._stability_tolerance_ms: int = 40  # allow slight under-run due to sample discretization
        self._recent_samples: list[Tuple[int, float, float, float]] = []  # (t_ms, x_mm, y_mm, |fz_n|)
        # Arming: require >= 50 N sustained for 2.0 s within the same cell (continuous)
        self._arming_window_ms: int = 2000
        self._arming_cell: Optional[Tuple[int, int]] = None
        self._arming_start_ms: Optional[int] = None

        # Automated tare scheduler state
        self._next_tare_due_ms: Optional[int] = None
        self._tare_dialog: Optional[TarePromptDialog] = None
        self._tare_active: bool = False
        self._tare_countdown_remaining_s: int = 0
        self._tare_last_tick_s: Optional[int] = None

        # Initialize live testing start button availability
        try:
            self._update_live_start_enabled()
        except Exception:
            pass

        # Reflect backend rate changes to controls
        try:
            self.controls.sampling_rate_changed.connect(self._on_sampling_rate_changed)
            self.controls.emission_rate_changed.connect(self._on_emission_rate_changed)
        except Exception:
            pass

        # Controller callbacks for model events
        self._request_model_metadata_cb: Optional[Callable[[str], None]] = None
        self._on_package_model_cb: Optional[Callable[[dict], None]] = None
        self._on_activate_model_cb: Optional[Callable[[str, str], None]] = None
        self._on_deactivate_model_cb: Optional[Callable[[str, str], None]] = None

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

    def on_sampling_rate_changed(self, slot: Callable[[int], None]) -> None:
        self._on_sampling_cb = slot

    def on_emission_rate_changed(self, slot: Callable[[int], None]) -> None:
        self._on_emission_cb = slot

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
            # Show dual-series legend only in mound mode
            is_mound = getattr(self, "state", None) is not None and getattr(self.state, "display_mode", "") == "mound"
            self.sensor_plot_left.set_dual_series_enabled(bool(is_mound))
            self.sensor_plot_right.set_dual_series_enabled(bool(is_mound))
            # Update live testing start enabled state on any config change
            self._update_live_start_enabled()
            self._log(f"config_changed: display_mode={self.state.display_mode}, selected_device_id={self.state.selected_device_id}, selected_device_type={self.state.selected_device_type}")
        except Exception:
            pass

    def _update_live_start_enabled(self) -> None:
        single_mode = (self.state.display_mode == "single")
        has_device = bool((self.state.selected_device_id or "").strip())
        enabled = bool(single_mode and has_device and self._live_session is None)
        if hasattr(self.controls, "live_testing_panel"):
            try:
                self.controls.live_testing_panel.btn_start.setEnabled(enabled)
                self._log(f"update_live_start_enabled: single_mode={single_mode}, has_device={has_device}, enabled={enabled}")
            except Exception:
                pass

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
        dlg = LiveTestSetupDialog(self)
        dlg.set_device_info(dev_id, model_id)
        dlg.set_defaults(tester="", body_weight_n=0.0)
        result = dlg.exec()
        self._log(f"setup_dialog_result: {result}")
        if result != LiveTestSetupDialog.Accepted:
            self._log("start_session: dialog cancelled")
            return
        tester, bw_n = dlg.get_values()
        self._log(f"setup_values: tester='{tester}', model_id={model_id}, device_id={dev_id}, bw_n={bw_n:.1f}")
        # Grid dimensions driven by config (canonical device space)
        rows, cols = config.GRID_DIMS_BY_MODEL.get(model_id, (3, 3))

        # Load per-model thresholds from config
        thresholds = Thresholds(
            dumbbell_tol_n=float(config.THRESHOLDS_DB_N_BY_MODEL.get(model_id, 6.0)),
            bodyweight_tol_n=float(config.THRESHOLDS_BW_N_BY_MODEL.get(model_id, 10.0)),
        )
        session = LiveTestSession(
            tester_name=tester,
            device_id=dev_id,
            model_id=model_id,
            body_weight_n=bw_n,
            thresholds=thresholds,
            grid_rows=rows,
            grid_cols=cols,
        )

        # Build 6 stages
        import math
        lb_to_n = 4.44822
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
            self.controls.live_testing_panel.set_next_stage_enabled(False)
            self.controls.live_testing_panel.set_next_stage_label("Next Stage")
            self.controls.live_testing_panel.set_metadata(tester, dev_id, model_id, bw_n)
            self.controls.live_testing_panel.set_thresholds(thresholds.dumbbell_tol_n, thresholds.bodyweight_tol_n)
            self.canvas_left.show_live_grid(rows, cols)
            self.canvas_right.show_live_grid(rows, cols)
            self.controls.live_testing_panel.set_stage_progress("Stage 1: 45 lb DB @ A", 0, rows * cols)
            # Request current model metadata for this device and reflect in panel
            try:
                self.controls.live_testing_panel.set_current_model("Loading…")
            except Exception:
                pass
            if self._request_model_metadata_cb and isinstance(dev_id, str) and dev_id.strip():
                try:
                    self._request_model_metadata_cb(dev_id)
                except Exception:
                    pass
            self._log(f"session_initialized: rows={rows}, cols={cols}, stages={len(self._live_session.stages)}")
        except Exception:
            pass

    def _on_next_stage(self) -> None:
        if self._live_session is None:
            return
        # Only proceed if current stage is fully completed
        try:
            stage = self._live_session.stages[self._live_stage_idx]
            completed = sum(1 for g in stage.results.values() if g.fz_mean_n is not None)
            if completed < stage.total_cells:
                return
        except Exception:
            return
        if self._live_stage_idx + 1 < len(self._live_session.stages):
            self._live_stage_idx += 1
            next_stage = self._live_session.stages[self._live_stage_idx]
            stage_text = f"Stage {next_stage.index}: {next_stage.name} @ {next_stage.location}"
            try:
                self.canvas_left.clear_live_colors()
                self.canvas_right.clear_live_colors()
                self.controls.live_testing_panel.set_stage_progress(stage_text, 0, next_stage.total_cells)
                self.controls.live_testing_panel.set_debug_status(f"{stage_text} – Arming…")
                self.controls.live_testing_panel.set_next_stage_enabled(False)
                # If this is the last stage (index == len(stages)), set button text to Finish after completion
                if self._live_stage_idx + 1 >= len(self._live_session.stages):
                    self.controls.live_testing_panel.set_next_stage_label("Finish")
                else:
                    self.controls.live_testing_panel.set_next_stage_label("Next Stage")
                self._active_cell = None
                self._recent_samples.clear()
                self._arming_cell = None
                self._arming_start_ms = None
            except Exception:
                pass
        else:
            # Last stage finished — show summary dialog
            try:
                tester = self._live_session.tester_name
                device_id = self._live_session.device_id
                model_id = self._live_session.model_id
                import datetime
                date_text = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                # Overall pass: at least 90% cells pass vs per-stage threshold; compute grade
                total_cells = 0
                pass_cells = 0
                model_id = (self._live_session.model_id or "06").strip()
                for st in self._live_session.stages:
                    is_db = (st.name.lower().find("db") >= 0)
                    base_tol = (config.THRESHOLDS_DB_N_BY_MODEL.get(model_id, 6.0) if is_db else config.THRESHOLDS_BW_N_BY_MODEL.get(model_id, 10.0))
                    for res in st.results.values():
                        if res.error_n is None:
                            continue
                        total_cells += 1
                        if abs(res.error_n) <= base_tol:
                            pass_cells += 1
                ratio = (pass_cells / max(1, total_cells))
                pass_fail = "Pass" if (ratio >= 0.895) else "Fail"  # round up: 89.5% counts as 90%
                # Letter grade with +/- (standard scale)
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
                dlg = LiveTestSummaryDialog(self)
                dlg.set_values(tester, device_id, model_id, date_text, pass_fail, pass_cells, total_cells, grade_text)
                if dlg.exec() == LiveTestSummaryDialog.Accepted:
                    edited_tester, _ = dlg.get_values()
                    try:
                        from ..ms_graph_excel import append_summary_row
                        append_summary_row(device_id, pass_fail, date_text, edited_tester, model_id)
                    except Exception as e:
                        try:
                            QtWidgets.QMessageBox.warning(self, "Export Failed", f"Could not append to Excel: {e}")
                        except Exception:
                            pass
            except Exception:
                pass
            self._on_live_end()

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
        try:
            if self._tare_dialog is not None:
                self._tare_dialog.reject()
        except Exception:
            pass
        self._tare_dialog = None
        try:
            self.controls.live_testing_panel.btn_start.setEnabled(True)
            self.controls.live_testing_panel.btn_end.setEnabled(False)
            self.controls.live_testing_panel.set_next_stage_enabled(False)
            self.controls.live_testing_panel.set_next_stage_label("Next Stage")
            self.controls.live_testing_panel.set_stage_progress("—", 0, 0)
            self.controls.live_testing_panel.set_telemetry(None, None, None, "—")
            self.canvas_left.hide_live_grid()
            self.canvas_right.hide_live_grid()
            try:
                self.controls.live_testing_panel.set_current_model("—")
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
        # Expect list of model metadata dicts; display a concise current model id if present
        try:
            models = list(data or [])
            model_text = "—"
            if models:
                # Prefer entry with location 'local' or 'both'; else first
                preferred = None
                for m in models:
                    loc = str((m or {}).get("location", "")).strip().lower()
                    if loc in ("local", "both"):
                        preferred = m
                        break
                chosen = preferred or models[0]
                model_text = str((chosen or {}).get("modelId") or "—")
            self.controls.live_testing_panel.set_current_model(model_text)
        except Exception:
            try:
                self.controls.live_testing_panel.set_current_model("—")
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
        # After activation/deactivation, refresh current model
        try:
            dev_id = (self.state.selected_device_id or "").strip()
            if dev_id and self._request_model_metadata_cb:
                self._request_model_metadata_cb(dev_id)
            # Update status label from status and re-enable controls
            try:
                s = status or {}
                st = str(s.get("status") or "").strip() if isinstance(s, dict) else str(s)
                msg = str(s.get("message") or "") if isinstance(s, dict) else ""
                text = f"{st.capitalize()}" + (f": {msg}" if msg else "")
                self.controls.live_testing_panel.set_model_status(text)
            except Exception:
                pass
            self.controls.live_testing_panel.set_model_controls_enabled(True)
        except Exception:
            pass

    def _on_activate_model(self, model_id: str) -> None:
        dev_id = (self.state.selected_device_id or "").strip()
        if dev_id and self._on_activate_model_cb:
            try:
                self._on_activate_model_cb(dev_id, model_id)
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

    def _on_live_single_snapshot(self, snap: Optional[Tuple[float, float, float, int, bool, float, float]]) -> None:
        if self._live_session is None or snap is None:
            return
        try:
            x_mm, y_mm, fz_n, t_ms, is_visible, raw_x_mm, raw_y_mm = snap
        except Exception:
            return

        # Evaluate scheduled tare guidance before modifying arming/stability state
        self._maybe_run_tare_guidance(fz_n, int(t_ms), bool(is_visible))
        # Pause arming/stabilization while tare dialog active
        if self._tare_active:
            # Keep telemetry updating but skip further processing
            return
        # Update telemetry
        stability = "tracking" if is_visible else "no load"
        try:
            self.controls.live_testing_panel.set_telemetry(fz_n, x_mm, y_mm, stability)
        except Exception:
            pass

        # Pause arming/stabilization while tare dialog active (telemetry was updated above)
        if self._tare_active:
            return

        # Do not process if not visible / below threshold
        if not is_visible:
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

        # Map COP (x_mm, y_mm) to grid cell using canonical device space (no rotation).
        # Config note: Width corresponds to world Y (right on screen), Height to world X (up on screen)
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
            else:
                w_mm = config.TYPE08_W_MM
                h_mm = config.TYPE08_H_MM
            half_w = w_mm / 2.0  # along world Y (left/right)
            half_h = h_mm / 2.0  # along world X (up/down)
            # If outside footprint, return None
            if abs(ry) > half_w or abs(rx) > half_h:
                return None
            # Column: left (-half_w) -> 0, right (+half_w) -> cols-1
            col_f = (ry + half_w) / w_mm * cols
            col_i = int(col_f)
            if col_i < 0:
                col_i = 0
            elif col_i >= cols:
                col_i = cols - 1
            # Row: top (+half_h) -> 0, bottom (-half_h) -> rows-1
            t = (half_h - rx) / h_mm  # 0 at top, 1 at bottom
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
        else:
            w_mm = config.TYPE08_W_MM
            h_mm = config.TYPE08_H_MM
        # Debug: show canonical fractions (no rotation) so they reflect assignment space
        rx_dbg, ry_dbg = x_mm, y_mm
        half_w = w_mm / 2.0
        half_h = h_mm / 2.0
        col_frac = (ry_dbg + half_w) / w_mm
        row_frac_top = (half_h - rx_dbg) / h_mm

        cell = to_cell_mm(x_mm, y_mm)
        if cell is None:
            # Not over the plate: reset arming guidance
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
                    if arm_span >= self._arming_window_ms:
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
                        base_tol = (config.THRESHOLDS_DB_N_BY_MODEL.get(model_id, 6.0) if is_db else config.THRESHOLDS_BW_N_BY_MODEL.get(model_id, 10.0))
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

                        # Progress update
                        completed = sum(1 for g in stage.results.values() if g.fz_mean_n is not None)
                        total = stage.total_cells
                        stage_text = f"Stage {stage.index}: {stage.name} @ {stage.location}"
                        self.controls.live_testing_panel.set_stage_progress(stage_text, completed, total)
                        # Enable Next Stage button when all cells are done
                        try:
                            if completed >= total:
                                self.controls.live_testing_panel.set_next_stage_enabled(True)
                        except Exception:
                            pass

                        # Reset window and active cell to allow next capture
                        self._recent_samples.clear()
                        self._active_cell = None
                        self.canvas_left.set_live_active_cell(None, None)
                        self.canvas_right.set_live_active_cell(None, None)
                        self.controls.live_testing_panel.set_debug_status("Captured. Move to next cell…")

                        # If stage completed, enable manual advance (do NOT auto-advance)
                        if completed >= total:
                            try:
                                self.controls.live_testing_panel.set_next_stage_enabled(True)
                                # Change button label to Finish on the last stage
                                if self._live_stage_idx + 1 >= len(self._live_session.stages):
                                    self.controls.live_testing_panel.set_next_stage_label("Finish")
                                self.controls.live_testing_panel.set_debug_status("Stage complete. Press Next Stage to continue…")
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
            self.controls.tare_requested.emit(gid)
            self._log("auto_tare: tare_requested emitted")
        except Exception:
            pass

    def _maybe_run_tare_guidance(self, fz_n: float, t_ms: int, is_visible: bool) -> None:
        # Only in live session and single-device mode
        if self._live_session is None:
            return
        try:
            # Initialize schedule if needed
            if self._next_tare_due_ms is None:
                self._next_tare_due_ms = int(t_ms) + int(getattr(config, "TARE_INTERVAL_S", 90)) * 1000

            # Update dialog if active
            if self._tare_active:
                try:
                    if self._tare_dialog is not None:
                        self._tare_dialog.set_force(float(fz_n))
                except Exception:
                    pass
                threshold = float(getattr(config, "TARE_STEP_OFF_THRESHOLD_N", 30.0))
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

            # Not active: check if due and safe to show (not mid-stabilization)
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
