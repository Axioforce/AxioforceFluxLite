from __future__ import annotations

from typing import Callable

from PySide6 import QtWidgets

from ..app_services.live_session_gate import LiveSessionGate
from .dialogs.tare_prompt import TarePromptDialog
from .dialogs.warmup_prompt import WarmupPromptDialog


class LiveSessionGateUi:
    """
    UI/controller wrapper around `LiveSessionGate`.

    Owns the warmup/tare dialogs and issues a hardware tare when the gate says so.
    """

    def __init__(
        self,
        *,
        parent: QtWidgets.QWidget,
        log: Callable[[str], None],
        tare: Callable[[], None],
        on_enter_active: Callable[[int], None],
        clear_gate_status: Callable[[], None],
    ) -> None:
        self._parent = parent
        self._log = log
        self._tare = tare
        self._on_enter_active = on_enter_active
        self._clear_gate_status = clear_gate_status

        self._gate = LiveSessionGate()
        self._phase_last: str = "inactive"

        self._warmup_dialog: WarmupPromptDialog | None = None
        self._tare_dialog: TarePromptDialog | None = None

    @property
    def phase(self) -> str:
        try:
            return str(getattr(self._gate, "phase", "inactive") or "inactive")
        except Exception:
            return "inactive"

    def is_active(self) -> bool:
        try:
            return bool(self._gate.is_active())
        except Exception:
            return False

    def reset(self, *, reason: str = "") -> None:
        """Reset warmup/tare gating state and close dialogs."""
        self._close_dialogs()
        try:
            self._gate.reset()
        except Exception:
            pass
        self._phase_last = "inactive"
        if reason:
            try:
                self._log(f"gate_reset: {reason}")
            except Exception:
                pass

    def start_session(self, *, warmup_duration_s: int = 20) -> None:
        """Begin warmup/tare gating for a new session and show warmup dialog."""
        try:
            self._gate.begin()
            self._phase_last = self._gate.phase
        except Exception:
            return

        try:
            dlg = WarmupPromptDialog(self._parent, duration_s=int(warmup_duration_s))
            dlg.set_waiting_for_trigger(True)
            dlg.set_remaining(int(warmup_duration_s))
            dlg.rejected.connect(self._on_warmup_dialog_dismissed)
            self._warmup_dialog = dlg
            dlg.show()
        except Exception:
            self._warmup_dialog = None

    def process_sample(self, *, t_ms: int, fz_abs_n: float, stage_switch_pending: bool) -> None:
        """Advance warmup/tare gating using the current force sample."""
        if stage_switch_pending:
            return
        if self.phase == "inactive":
            return

        info = self._gate.update(now_ms=int(t_ms), fz_abs_n=float(fz_abs_n))
        phase = str(info.get("phase") or "inactive")

        # Phase transitions drive dialog lifecycle.
        if phase != self._phase_last:
            self._phase_last = phase
            try:
                self._log(f"gate_phase -> {phase}")
            except Exception:
                pass

            # Close warmup dialog when leaving warmup.
            if phase != "warmup":
                self._safe_close_dialog(self._warmup_dialog)
                self._warmup_dialog = None

            # Show tare dialog when entering tare.
            if phase == "tare":
                try:
                    td = TarePromptDialog(self._parent)
                    td.rejected.connect(self._on_tare_dialog_dismissed)
                    self._tare_dialog = td
                    td.set_force(float(fz_abs_n))
                    td.set_countdown(15)
                    td.show()
                except Exception:
                    self._tare_dialog = None

            # Close tare dialog when entering active.
            if phase == "active":
                self._safe_close_dialog(self._tare_dialog)
                self._tare_dialog = None
                try:
                    self._on_enter_active(int(t_ms))
                except Exception:
                    pass
                try:
                    self._clear_gate_status()
                except Exception:
                    pass

        # Update dialog contents.
        if phase == "warmup":
            try:
                triggered = bool(info.get("warmup_triggered"))
                remaining = info.get("warmup_remaining_s")
                if self._warmup_dialog is not None:
                    self._warmup_dialog.set_waiting_for_trigger(not triggered)
                    self._warmup_dialog.set_remaining(int(remaining) if remaining is not None else 20)
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

        # Fire tare exactly once when the gate says so.
        if bool(info.get("should_tare")):
            try:
                self._tare()
                self._log("gate_tare: issued hardware tare")
            except Exception:
                pass

    def _on_warmup_dialog_dismissed(self) -> None:
        """
        User dismissed warmup dialog (X / Esc).
        Treat warmup as done and proceed to tare.
        """
        try:
            if self.phase == "warmup":
                self._log("gate_skip: warmup dismissed -> tare")
                self._gate.skip_warmup()
        except Exception:
            pass

    def _on_tare_dialog_dismissed(self) -> None:
        """
        User dismissed tare dialog (X / Esc).
        Treat tare stage as done WITHOUT taring, and allow testing.
        """
        try:
            if self.phase == "tare":
                self._log("gate_skip: tare dismissed -> active (no tare)")
                self._gate.skip_tare()
                try:
                    self._clear_gate_status()
                except Exception:
                    pass
        except Exception:
            pass

    def _close_dialogs(self) -> None:
        self._safe_close_dialog(self._warmup_dialog)
        self._safe_close_dialog(self._tare_dialog)
        self._warmup_dialog = None
        self._tare_dialog = None

    def _safe_close_dialog(self, dlg: QtWidgets.QDialog | None) -> None:
        """Close a gate dialog, ensuring it can't end the session."""
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

