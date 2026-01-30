from __future__ import annotations

from typing import Any, Optional, Protocol

from ..domain.models import TestResult


class _Canvas(Protocol):
    def get_rotation_quadrants(self) -> int: ...

    def set_live_active_cell(self, row: int | None, col: int | None) -> None: ...

    def set_live_status(self, status: object | None) -> None: ...


class _LiveTestingPanel(Protocol):
    def set_stage_progress(self, stage_name: str, completed: int, total: int) -> None: ...

    def set_stage_summary(self, stages: list, *, grid_total_cells: int | None, current_stage_index: int) -> None: ...


class _Controls(Protocol):
    live_testing_panel: _LiveTestingPanel


class _TestingService(Protocol):
    current_session: Any

    current_stage_index: int

    def record_result(self, stage_idx: int, row: int, col: int, result: TestResult) -> None: ...


class _Controller(Protocol):
    testing: _TestingService


class _State(Protocol):
    display_mode: str
    selected_device_id: str
    selected_device_type: str


class _PeriodicTare(Protocol):
    pending: bool


class _Host(Protocol):
    controller: _Controller
    state: _State
    controls: _Controls

    canvas_left: _Canvas
    canvas_right: _Canvas

    _stage_switch_pending: bool
    _periodic_tare: _PeriodicTare

    def _set_live_status_bar_state(self, *, mode: str, text: str, pct: int | None) -> None: ...

    def _update_live_stage_nav(self, reason: str = "") -> None: ...

    def _apply_temp_test_cell_color(self, stage: object, row: int, col: int) -> None: ...

    def _maybe_auto_switch_temp_test_stage(self, sess: object, current_stage_idx: int, completed: int, total: int) -> None: ...


class _LiveMeasurementEngine(Protocol):
    active_cell: tuple[int, int] | None
    phase: str
    progress_01: float

    def process_sample(
        self,
        *,
        t_ms: int,
        cop_x_mm: float,
        cop_y_mm: float,
        fz_n: float,
        is_visible: bool,
        device_type: str,
        rows: int,
        cols: int,
        rotation_quadrants: int,
        is_cell_already_done,
    ) -> Any: ...


class LiveMeasurementUi:
    """
    UI orchestration around the `LiveMeasurementEngine`:
    - gates sample processing based on session + UI state
    - updates active-cell highlight + status bar
    - commits captured results into the session and updates progress/summary
    """

    def __init__(self, *, engine: _LiveMeasurementEngine) -> None:
        self._engine = engine
        self._active_cell_last: tuple[int, int] | None = None

    def reset_ui_state(self) -> None:
        self._active_cell_last = None

    def process_sample(
        self,
        host: _Host,
        *,
        t_ms: int,
        cop_x_m: float,
        cop_y_m: float,
        fz_n: float,
        is_visible: bool,
    ) -> None:
        sess = host.controller.testing.current_session
        if not sess:
            return

        # Do not capture measurements while stage switch dialog is showing.
        if bool(getattr(host, "_stage_switch_pending", False)):
            return

        # Do not capture measurements while periodic tare dialog is showing.
        try:
            if bool(getattr(host._periodic_tare, "pending", False)):
                return
        except Exception:
            return

        # Do not change discrete-temp capture behavior yet (it has its own flow).
        if bool(getattr(sess, "is_discrete_temp", False)):
            return

        if str(getattr(host.state, "display_mode", "") or "") != "single":
            return

        selected_id = str(getattr(host.state, "selected_device_id", "") or "").strip()
        if not selected_id:
            return

        # Session is tied to a specific device id.
        if str(getattr(sess, "device_id", "") or "").strip() != selected_id:
            return

        try:
            stage_idx = int(getattr(host.controller.testing, "current_stage_index", 0))
        except Exception:
            stage_idx = 0

        try:
            stage = (getattr(sess, "stages", []) or [])[stage_idx]
        except Exception:
            return

        dev_type = (
            str(getattr(host.state, "selected_device_type", "") or "").strip()
            or str(getattr(sess, "model_id", "") or "").strip()
            or "06"
        )
        rows = int(getattr(sess, "grid_rows", 0) or 0)
        cols = int(getattr(sess, "grid_cols", 0) or 0)
        if rows <= 0 or cols <= 0:
            return

        # COP from backend is in meters (renderer converts m->mm). Convert here too.
        cop_x_mm = float(cop_x_m) * 1000.0
        cop_y_mm = float(cop_y_m) * 1000.0

        try:
            rot_k = int(host.canvas_left.get_rotation_quadrants())
        except Exception:
            rot_k = 0

        def _already_done(r: int, c: int) -> bool:
            try:
                existing = (getattr(stage, "results", {}) or {}).get((int(r), int(c)))
                return bool(existing and getattr(existing, "fz_mean_n", None) is not None)
            except Exception:
                return False

        ev = self._engine.process_sample(
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

        # Active cell highlight (throttled to changes).
        try:
            active = self._engine.active_cell
            if active != self._active_cell_last:
                self._active_cell_last = active
                if active is None:
                    host.canvas_left.set_live_active_cell(None, None)
                    host.canvas_right.set_live_active_cell(None, None)
                else:
                    host.canvas_left.set_live_active_cell(int(active[0]), int(active[1]))
                    host.canvas_right.set_live_active_cell(int(active[0]), int(active[1]))
        except Exception:
            pass

        # Bottom status bar (arming/measuring).
        try:
            phase = str(getattr(self._engine, "phase", "idle") or "idle")
            prog = float(getattr(self._engine, "progress_01", 0.0) or 0.0)
            pct = int(max(0, min(100, round(100.0 * prog))))
            if phase == "arming":
                host._set_live_status_bar_state(mode="arming", text="Arming...", pct=pct)
            elif phase == "measuring":
                host._set_live_status_bar_state(mode="measuring", text="Taking measurement...", pct=pct)
            else:
                host._set_live_status_bar_state(mode="idle", text="", pct=None)
        except Exception:
            host._set_live_status_bar_state(mode="idle", text="", pct=None)

        # Keep overlay status empty for a cleaner plate view.
        try:
            host.canvas_left.set_live_status(None)
        except Exception:
            pass

        if not ev:
            return

        # Captured: clear the status bar so it doesn't linger.
        host._set_live_status_bar_state(mode="idle", text="", pct=None)

        # Commit captured measurement into the session via TestingService.
        res = TestResult(
            row=int(ev.row),
            col=int(ev.col),
            fz_mean_n=float(ev.mean_fz_n),
            cop_x_mm=float(ev.mean_cop_x_mm),
            cop_y_mm=float(ev.mean_cop_y_mm),
        )
        host.controller.testing.record_result(int(stage_idx), int(ev.row), int(ev.col), res)

        # For Temperature Test mode: use stage-specific colors (no pass/fail).
        is_temp_test = bool(getattr(sess, "is_temp_test", False))
        if is_temp_test:
            host._apply_temp_test_cell_color(stage, int(ev.row), int(ev.col))

        # Update progress/Next button using existing LiveTestingPanel helpers.
        try:
            completed = 0
            for r in (getattr(stage, "results", {}) or {}).values():
                try:
                    if r is not None and getattr(r, "fz_mean_n", None) is not None:
                        completed += 1
                except Exception:
                    continue

            total = int(getattr(stage, "total_cells", rows * cols) or (rows * cols))
            stage_text = str(getattr(stage, "name", "") or "Stage")
            host.controls.live_testing_panel.set_stage_progress(stage_text, completed, total)

            # Navigation gating handled elsewhere.
            host._update_live_stage_nav("cell_captured")

            # Also update the per-stage summary tracker (Location A/B overview).
            try:
                sess2 = getattr(host.controller.testing, "current_session", None)
                if sess2 and not bool(getattr(sess2, "is_discrete_temp", False)):
                    total2 = int(getattr(sess2, "grid_rows", 0) or 0) * int(getattr(sess2, "grid_cols", 0) or 0)
                    host.controls.live_testing_panel.set_stage_summary(
                        getattr(sess2, "stages", []) or [],
                        grid_total_cells=total2 if total2 > 0 else None,
                        current_stage_index=int(stage_idx),
                    )
            except Exception:
                pass

            # Temperature Test mode: auto-switch stages after 2 cells or stage completion.
            if is_temp_test:
                host._maybe_auto_switch_temp_test_stage(sess, stage_idx, completed, total)
        except Exception:
            pass

