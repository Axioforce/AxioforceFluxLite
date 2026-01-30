from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class LiveSessionGateConfig:
    warmup_trigger_fz_n: float = 50.0
    warmup_duration_s: int = 20

    tare_threshold_fz_n: float = 50.0
    tare_duration_s: int = 15


class LiveSessionGate:
    """
    Small state machine that gates measurement until:
    - Warmup has been triggered (Fz>=threshold once) and 20s has elapsed
    - Then user stays off the plate (Fz<threshold continuously) for 15s
    - Then a tare should be initiated and the session becomes active.

    This is UI-agnostic: it only reports timers and phase.
    """

    def __init__(self, cfg: LiveSessionGateConfig | None = None) -> None:
        self.cfg = cfg or LiveSessionGateConfig()
        self.reset()

    def reset(self) -> None:
        self.phase: str = "inactive"  # inactive|warmup|tare|active
        self._warmup_start_ms: Optional[int] = None
        self._tare_off_start_ms: Optional[int] = None
        self._tare_fired: bool = False

    def begin(self) -> None:
        """Begin warmup+ tare sequence."""
        self.reset()
        self.phase = "warmup"

    def skip_warmup(self) -> None:
        """
        User-driven override: treat warmup as completed and proceed to tare phase.

        This does NOT fire a tare; it only advances the gate.
        """
        if self.phase != "warmup":
            return
        self.phase = "tare"
        self._tare_off_start_ms = None

    def skip_tare(self) -> None:
        """
        User-driven override: treat tare phase as completed and proceed to active.

        IMPORTANT: This intentionally does NOT request a hardware tare.
        """
        if self.phase != "tare":
            return
        self._tare_fired = True
        self.phase = "active"

    def is_active(self) -> bool:
        return self.phase == "active"

    def warmup_triggered(self) -> bool:
        return self._warmup_start_ms is not None

    def warmup_remaining_s(self, now_ms: int) -> Optional[int]:
        if self.phase != "warmup" or self._warmup_start_ms is None:
            return None
        elapsed_ms = max(0, int(now_ms) - int(self._warmup_start_ms))
        elapsed_s = int(elapsed_ms // 1000)
        rem = int(self.cfg.warmup_duration_s) - int(elapsed_s)
        return max(0, int(rem))

    def tare_remaining_s(self, now_ms: int) -> Optional[int]:
        if self.phase != "tare" or self._tare_off_start_ms is None:
            return None
        elapsed_ms = max(0, int(now_ms) - int(self._tare_off_start_ms))
        elapsed_s = int(elapsed_ms // 1000)
        rem = int(self.cfg.tare_duration_s) - int(elapsed_s)
        return max(0, int(rem))

    def update(self, *, now_ms: int, fz_abs_n: float) -> dict:
        """
        Advance state machine.

        Returns a dict with:
        - phase: str
        - warmup_triggered: bool
        - warmup_remaining_s: Optional[int]
        - tare_remaining_s: Optional[int]
        - should_tare: bool (true exactly once when tare should be executed)
        """
        now_ms_i = int(now_ms)
        fz_abs = float(abs(fz_abs_n))

        should_tare = False

        if self.phase == "inactive":
            return {
                "phase": self.phase,
                "warmup_triggered": False,
                "warmup_remaining_s": None,
                "tare_remaining_s": None,
                "should_tare": False,
            }

        if self.phase == "warmup":
            # Trigger start once when force crosses threshold.
            if self._warmup_start_ms is None and fz_abs >= float(self.cfg.warmup_trigger_fz_n):
                self._warmup_start_ms = now_ms_i

            rem = self.warmup_remaining_s(now_ms_i)
            if rem is not None and rem <= 0:
                # Warmup complete -> transition to tare
                self.phase = "tare"
                self._tare_off_start_ms = None

        if self.phase == "tare":
            # Require continuous "off plate" time under threshold.
            if fz_abs >= float(self.cfg.tare_threshold_fz_n):
                self._tare_off_start_ms = None
            else:
                if self._tare_off_start_ms is None:
                    self._tare_off_start_ms = now_ms_i

            rem = self.tare_remaining_s(now_ms_i)
            if rem is not None and rem <= 0 and not self._tare_fired:
                self._tare_fired = True
                should_tare = True
                self.phase = "active"

        return {
            "phase": self.phase,
            "warmup_triggered": bool(self._warmup_start_ms is not None),
            "warmup_remaining_s": self.warmup_remaining_s(now_ms_i),
            "tare_remaining_s": self.tare_remaining_s(now_ms_i),
            "should_tare": bool(should_tare),
        }

