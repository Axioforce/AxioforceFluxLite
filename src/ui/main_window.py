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
        self.bridge.single_snapshot_ready.connect(self._on_live_single_snapshot)
        self.bridge.plate_device_id_ready.connect(self.set_plate_device_id)
        self.bridge.available_devices_ready.connect(self.set_available_devices)
        self.bridge.active_devices_ready.connect(self.update_active_devices)
        self.bridge.force_vector_ready.connect(self._on_force_vector)
        self.bridge.moments_ready.connect(self._on_moments_ready)
        self.bridge.mound_force_vectors_ready.connect(self._on_mound_force_vectors)

        # Live testing wiring (bottom panel)
        try:
            self.controls.live_testing_panel.start_session_requested.connect(self._on_live_start)
            self.controls.live_testing_panel.end_session_requested.connect(self._on_live_end)
            self.controls.live_testing_panel.next_stage_requested.connect(self._on_next_stage)
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

        # Initialize live testing start button availability
        try:
            self._update_live_start_enabled()
        except Exception:
            pass

    def _log(self, msg: str) -> None:
        try:
            print(f"[live] {msg}", flush=True)
        except Exception:
            pass

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
        rows, cols = GRID_BY_MODEL.get(model_id, (3, 3))

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
            self.controls.live_testing_panel.set_debug_status("Arming… (need ≥50 N for 2.0 s in one cell)")
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
                    self._submit_summary_to_google_sheets(edited_tester, device_id, model_id, date_text, pass_fail)
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
        try:
            self.controls.live_testing_panel.btn_start.setEnabled(True)
            self.controls.live_testing_panel.btn_end.setEnabled(False)
            self.controls.live_testing_panel.set_next_stage_enabled(False)
            self.controls.live_testing_panel.set_next_stage_label("Next Stage")
            self.controls.live_testing_panel.set_stage_progress("—", 0, 0)
            self.controls.live_testing_panel.set_telemetry(None, None, None, "—")
            self.canvas_left.hide_live_grid()
            self.canvas_right.hide_live_grid()
            self.controls.live_testing_panel.set_debug_status(None)
        except Exception:
            pass

    def _submit_summary_to_google_sheets(self, tester: str, device_id: str, model_id: str, date_text: str, pass_fail: str) -> None:
        try:
            import gspread
            from google.oauth2.service_account import Credentials
            # NOTE: For production, read these from a secure location, not inline
            service_account_info = {
                "type": "service_account",
                "project_id": "axioforcelivetesting",
                "private_key_id": "4cc5076dea648823e429722a33be61ff72f0fa6f",
                "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvAIBADANBgkqhkiG9w0BAQEFAASCBKYwggSiAgEAAoIBAQDCVuVmiLG7kjzH\nyUXQeg5GCSCl2lsFfBMJTHIC+448Pcwm/b7TM/n0Y3twZjerdWrrmp3J4z78QbQ6\njf9J4jya9tFD4PpKkInJvqG3V8ruHJ5pjh6oQsSJ5SrYduwbpn5tPBAOwdOmtKUW\ncSDH+S31tt/t4KcuTUyb+fEUs4FygerqFxoLtv0sQOb5XK6R+nKi3wOkLE4l0v1U\nxxBY4o19V8+0x53T5cvEURFUiMAacqT89cLBFwNoCFk9rz8FlD106m/ppQxoxcHw\nbg0IJfmcOhxDmNl51zypbp7M27+22PWIUhUPOIIPcrNLp84ftjEOO+/EGW4VISrz\nwfdtsVi5AgMBAAECggEABpUCMLEr2999wYKBTn35InQdWqCvo9rqRiQEWdzX72sP\nEWRXZI3bxw7g174OiqHFHPUKp74o8aBEEMD4cYcrvaU4Zzp6dQYPiflelCLGvmkn\nuwl+OP1cl2hfRXTvAEITrB10VHD00IOecnPNxBgeGgwMlODz/e8joMYcB93LN5aR\nwF+ZPXR2SuKVlFJAdybAZjylIw4lldh5koLWWJvLU/JCo/H1Uko3BK7+1chTQrX1\nTTNVUy+XoRZNNNOxpdvB1qXkMTWUPeArliyVJtZubYNhRdgCyUm4kWVyGNJ6lll9\n6aAt5SnKXyCyVBXy5nI+YBgl3sWgGau0miyvyOXpkQKBgQDn966sqqYoh6ud72Ao\n4tBA83XrdFnFeWs1VFjRxozpBiAlTIGgySmz2kqmjXJxZi9wCkOXYTempMtUecR+\nr+oMVnqhMCKi5zew8iVlZCQs+G//h6sKMXBzkFOq+qAYIz/DHccYiuVj5lVSKI/z\nF14Acp4jAeXvLDNg4/wlK3VANQKBgQDWeTrWq/NzL2tdscbDaua85Jy4axvtSGex\n0ixn7LNgcwk7+MsDW0pXA0gFHkxukfvGq/ZY6NIBTWox9fucq+zjm2leCHY2NR5o\n1khFcbF1dGjzMERQOoqh1T/q1TQmMXD0PQOt+Wxt50CA1xhvEvoZWT1ixvMT9l5I\nte8JyBgO9QKBgHoiNLwA1Z99X2S2hoDAeznXdfzUs/d/aG0ZzfIVglemvAIneBD6\nGZTymF99FgaS8OMi5Fet/ikll1ERE95ILQj193cq6vGun+nwdLQft9RdskpuWiXx\nxe1yzjq13tkWphnLceqAJyskOUQay0AIy5ucvZpdA32cXijjoPzJFuEJAoGAaTTG\nnA91OIeGT0upiKqjzP0Hs58278qYsy26ArClvSYw3W5Jh7f8W3qMlZYrQAH0U5x/\nF1X9zg2/jgpwBoZ/iZbutOXJtwWPiTWz9fyzZD5aTRDcMc7FumT1GajEEAgotGZJ\nq8myWqcZiRn6LmJMtKqF5jJZgu1Tiq9UNqQkyRECgYBFNt26nnuYSkiyovZBKonx\nCYWoJ6CRexQmO3lAJZazrDSsFhI7/qaoYjX6DxLqcMvUjACSNFraN6zo7ZjEnbGw\nQ3cPaC+kWs9eZ8xIE7IIGHcVPZKagivx6fZJLin3N98iVg0yK1oavFyMis4A3fa1\nLQ/rIuLwmskckbL26HQ28A==\n-----END PRIVATE KEY-----\n",
                "client_email": "axioforcelivetesting@axioforcelivetesting.iam.gserviceaccount.com",
                "client_id": "116599978791633967517",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/axioforcelivetesting%40axioforcelivetesting.iam.gserviceaccount.com",
                "universe_domain": "googleapis.com",
            }
            scopes = ["https://www.googleapis.com/auth/spreadsheets"]
            creds = Credentials.from_service_account_info(service_account_info, scopes=scopes)
            gc = gspread.authorize(creds)
            sh = gc.open_by_key("19C2NSiFtHGEnQruVpMQ8m5-mkRvAnsLHOGT36U_WvvY")
            ws = sh.worksheet("Sheet1")
            # Columns: Plate ID, Pass/Fail, Date, Tester, Model ID
            ws.append_row([device_id, pass_fail, date_text, tester, model_id], value_input_option="USER_ENTERED")
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

    def _on_live_single_snapshot(self, snap: Optional[Tuple[float, float, float, int, bool, float, float]]) -> None:
        if self._live_session is None or snap is None:
            return
        try:
            x_mm, y_mm, fz_n, t_ms, is_visible, raw_x_mm, raw_y_mm = snap
        except Exception:
            return
        # Update telemetry
        stability = "tracking" if is_visible else "no load"
        try:
            self.controls.live_testing_panel.set_telemetry(fz_n, x_mm, y_mm, stability)
        except Exception:
            pass

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

        # Map COP (x_mm, y_mm) to grid cell using real plate dimensions.
        # Config note: Width corresponds to world Y (right on screen), Height to world X (up on screen)
        def to_cell_mm(x_mm_val: float, y_mm_val: float) -> Optional[Tuple[int, int]]:
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
            if abs(y_mm_val) > half_w or abs(x_mm_val) > half_h:
                return None
            # Column: left (-half_w) -> 0, right (+half_w) -> cols-1
            col_f = (y_mm_val + half_w) / w_mm * cols
            col_i = int(col_f)
            if col_i < 0:
                col_i = 0
            elif col_i >= cols:
                col_i = cols - 1
            # Row: top (+half_h) -> 0, bottom (-half_h) -> rows-1
            t = (half_h - x_mm_val) / h_mm  # 0 at top, 1 at bottom
            row_f = t * rows
            row_i = int(row_f)
            if row_i < 0:
                row_i = 0
            elif row_i >= rows:
                row_i = rows - 1
            # Observed orientation indicates an anti-diagonal mirror; correct by mapping
            # (row, col) -> (rows-1-col, cols-1-row)
            corr_row = rows - 1 - col_i
            corr_col = cols - 1 - row_i
            return (corr_row, corr_col)

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
        half_w = w_mm / 2.0
        half_h = h_mm / 2.0
        col_frac = (y_mm + half_w) / w_mm
        row_frac_top = (half_h - x_mm) / h_mm

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

