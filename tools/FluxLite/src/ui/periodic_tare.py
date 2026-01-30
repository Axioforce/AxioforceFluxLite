from __future__ import annotations

from typing import Callable

from PySide6 import QtWidgets

from .dialogs.tare_prompt import TarePromptDialog


class PeriodicTareController:
    """
    Periodic tare UX + timing.

    - Only intended to run while a live session gate is active ("active" phase)
    - Shows a dialog prompting the user to get off the plate (force < 50N)
    - Once force stays < 50N for 15s, issues a hardware tare
    """

    def __init__(
        self,
        *,
        parent: QtWidgets.QWidget,
        tare: Callable[[], None],
        log: Callable[[str], None],
        get_stream_time_last_ms: Callable[[], int],
        interval_ms: int = 90_000,
    ) -> None:
        self._parent = parent
        self._tare = tare
        self._log = log
        self._get_stream_time_last_ms = get_stream_time_last_ms

        self.interval_ms: int = int(interval_ms)

        self._last_ms: int = 0
        self._pending: bool = False
        self._countdown_start_ms: int = 0
        self._dialog: TarePromptDialog | None = None
        self._due: bool = False
        self._waiting_cell: object | None = None

    @property
    def pending(self) -> bool:
        return bool(self._pending)

    def start(self, t_ms: int) -> None:
        """Start/restart the periodic timer (typically when gate enters active)."""
        self._last_ms = int(t_ms or 0)
        self._pending = False
        self._countdown_start_ms = 0
        self._due = False
        self._waiting_cell = None
        self._close_dialog()

    def reset(self) -> None:
        """Reset all state (typically when session ends)."""
        self._close_dialog()
        self._last_ms = 0
        self._pending = False
        self._countdown_start_ms = 0
        self._due = False
        self._waiting_cell = None

    def tick(
        self,
        *,
        t_ms: int,
        fz_abs_n: float,
        gate_phase: str,
        stage_switch_pending: bool,
        live_meas_phase: str,
        live_meas_active_cell: object | None,
    ) -> None:
        """Evaluate state and show/update/close periodic tare dialog as needed."""
        if gate_phase != "active":
            return
        if stage_switch_pending:
            return

        # If periodic tare dialog is already showing, update it.
        if self._pending:
            self._update_dialog(t_ms=t_ms, fz_abs_n=fz_abs_n)
            return

        # If we're waiting for measurement to complete before showing tare.
        if self._due:
            self._check_waiting(t_ms=t_ms, live_meas_phase=live_meas_phase, live_meas_active_cell=live_meas_active_cell)
            return

        # If not started yet (e.g. missed start()), initialize to "now" and don't fire immediately.
        if self._last_ms <= 0:
            self._last_ms = int(t_ms or 0)
            return

        elapsed = int(t_ms) - int(self._last_ms)
        if elapsed < int(self.interval_ms):
            return

        # If we're mid-arming or mid-measurement, don't interrupt—mark as due and wait.
        if live_meas_phase in ("arming", "measuring") and live_meas_active_cell is not None:
            self._due = True
            self._waiting_cell = live_meas_active_cell
            self._log(f"Periodic tare due but waiting (phase={live_meas_phase}, cell={live_meas_active_cell})")
            return

        self._show_dialog(t_ms=t_ms)

    def _check_waiting(self, *, t_ms: int, live_meas_phase: str, live_meas_active_cell: object | None) -> None:
        # If measurement completed (phase is idle), show the dialog.
        if live_meas_phase == "idle":
            self._log("Periodic tare: measurement completed, showing dialog")
            self._due = False
            self._waiting_cell = None
            self._show_dialog(t_ms=t_ms)
            return

        # If the cell changed while we were waiting, force tare immediately.
        if self._waiting_cell is not None and live_meas_active_cell != self._waiting_cell:
            self._log(f"Periodic tare: cell changed from {self._waiting_cell} to {live_meas_active_cell}, forcing tare")
            self._due = False
            self._waiting_cell = None
            self._show_dialog(t_ms=t_ms)

    def _show_dialog(self, *, t_ms: int) -> None:
        if self._pending:
            return

        self._pending = True
        self._countdown_start_ms = 0  # Will be set when force < 50N

        try:
            dlg = TarePromptDialog(self._parent)
            dlg.setWindowTitle("Periodic Tare")
            dlg.rejected.connect(self._on_dialog_dismissed)
            self._dialog = dlg
            dlg.set_countdown(15)
            dlg.show()
            self._log("Periodic tare dialog shown")
        except Exception:
            self._pending = False
            self._dialog = None

    def _update_dialog(self, *, t_ms: int, fz_abs_n: float) -> None:
        if not self._pending or self._dialog is None:
            return

        try:
            self._dialog.set_force(float(fz_abs_n))
        except Exception:
            pass

        # Check if force is below 50N.
        if float(fz_abs_n) < 50.0:
            # Start or continue countdown.
            if self._countdown_start_ms == 0:
                self._countdown_start_ms = int(t_ms)
                self._log("Periodic tare countdown started (force < 50N)")

            elapsed_s = (int(t_ms) - int(self._countdown_start_ms)) / 1000.0
            remaining_s = max(0, 15 - int(elapsed_s))

            try:
                self._dialog.set_countdown(remaining_s)
            except Exception:
                pass

            if elapsed_s >= 15.0:
                self._complete(t_ms=t_ms)
        else:
            # Force went back above 50N, reset countdown.
            if self._countdown_start_ms != 0:
                self._log("Periodic tare countdown reset (force >= 50N)")
            self._countdown_start_ms = 0
            try:
                self._dialog.set_countdown(15)
            except Exception:
                pass

    def _complete(self, *, t_ms: int) -> None:
        """Issue tare and close dialog; reset timer for next periodic tare."""
        try:
            self._tare()
            self._log("Periodic tare: issued hardware tare")
        except Exception:
            pass

        self._close_dialog()
        self._last_ms = int(t_ms or 0)

    def _on_dialog_dismissed(self) -> None:
        """User dismissed the periodic tare dialog (X / Esc)."""
        self._log("Periodic tare dialog dismissed by user (skipping tare)")
        self._close_dialog()
        # Reset timer anyway so they get another chance in `interval_ms`.
        try:
            self._last_ms = int(self._get_stream_time_last_ms() or 0)
        except Exception:
            pass

    def _close_dialog(self) -> None:
        try:
            if self._dialog is not None:
                try:
                    self._dialog.rejected.disconnect()
                except Exception:
                    pass
                self._dialog.close()
        except Exception:
            pass
        self._dialog = None
        self._pending = False
        self._countdown_start_ms = 0

