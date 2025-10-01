from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from . import config


LAUNCH_NAME = "Launch Zone"
LANDING_NAME = "Landing Zone"


def _ewma(prev: Optional[float], new: float, alpha: float) -> float:
    if prev is None:
        return new
    return alpha * new + (1.0 - alpha) * prev


@dataclass
class DeviceState:
    cop_x_mm: float = 0.0
    cop_y_mm: float = 0.0
    fz_total_n: float = 0.0
    last_time_ms: int = 0
    is_visible: bool = False
    raw_cop_x_mm: float = 0.0
    raw_cop_y_mm: float = 0.0

    # Smoothed values
    smoothed_cop_x_mm: Optional[float] = None
    smoothed_cop_y_mm: Optional[float] = None
    smoothed_fz_total_n: Optional[float] = None

    def update(self, cop_x_mm: float, cop_y_mm: float, fz_total_n: float, time_ms: int, alpha: float) -> None:
        self.cop_x_mm = cop_x_mm
        self.cop_y_mm = cop_y_mm
        self.fz_total_n = fz_total_n
        self.last_time_ms = time_ms
        self.is_visible = True

        self.smoothed_cop_x_mm = _ewma(self.smoothed_cop_x_mm, cop_x_mm, alpha)
        self.smoothed_cop_y_mm = _ewma(self.smoothed_cop_y_mm, cop_y_mm, alpha)
        self.smoothed_fz_total_n = _ewma(self.smoothed_fz_total_n, fz_total_n, alpha)

    def snapshot(self) -> Tuple[float, float, float, int, bool, float, float]:
        x = self.smoothed_cop_x_mm if self.smoothed_cop_x_mm is not None else self.cop_x_mm
        y = self.smoothed_cop_y_mm if self.smoothed_cop_y_mm is not None else self.cop_y_mm
        fz = self.smoothed_fz_total_n if self.smoothed_fz_total_n is not None else self.fz_total_n
        return x, y, fz, self.last_time_ms, self.is_visible, self.raw_cop_x_mm, self.raw_cop_y_mm


@dataclass
class Model:
    devices: Dict[str, DeviceState] = field(default_factory=lambda: {LAUNCH_NAME: DeviceState(), LANDING_NAME: DeviceState()})
    last_msg_time_ms: Optional[int] = None
    ema_hz: Optional[float] = None

    def identify_position(self, device_id: str) -> Optional[str]:
        # device_id looks like "<group>.<virtual-name>"
        token = (device_id or "").lower().strip()
        name = token.split(".")[-1].strip()  # last segment only
        if name == "launch zone":
            return LAUNCH_NAME
        if name == "landing zone":
            return LANDING_NAME
        return None

    def update_from_payload(self, payload: dict, alpha: float, fz_threshold: float) -> Optional[str]:
        device_id = payload.get("device_id") or payload.get("deviceId") or ""
        pos = self.identify_position(device_id)
        if pos is None:
            return None

        sensors = payload.get("sensors") or []
        fz_total = 0.0
        # Prefer a precomputed "Sum" entry if provided to avoid double-counting
        sum_entry = None
        for s in sensors:
            if str(s.get("name", "")).strip().lower() == "sum":
                sum_entry = s
                break
        if sum_entry is not None:
            try:
                fz_total = float(sum_entry.get("z", 0.0))
            except Exception:
                fz_total = 0.0
        else:
            for s in sensors:
                try:
                    fz_total += float(s.get("z", 0.0))
                except Exception:
                    continue

        cop = payload.get("cop") or {}
        cop_x_mm = float(cop.get("x", 0.0)) * 1000.0
        cop_y_mm = float(cop.get("y", 0.0)) * 1000.0
        raw_x_mm, raw_y_mm = cop_x_mm, cop_y_mm
        # Launch Zone: invert X axis
        if pos == LAUNCH_NAME:
            cop_x_mm = -cop_x_mm
        time_ms = int(payload.get("time", 0))

        # Landing Zone (unused for now): keep previous orientation fix if drawn later
        if pos == LANDING_NAME:
            cop_x_mm = -cop_x_mm
            cop_y_mm = cop_y_mm + config.LANDING_MID_Y_MM
        # Update values and apply 22 N noise filter for visibility
        self.devices[pos].raw_cop_x_mm = raw_x_mm
        self.devices[pos].raw_cop_y_mm = raw_y_mm
        self.devices[pos].update(cop_x_mm, cop_y_mm, fz_total, time_ms, alpha)
        self.devices[pos].is_visible = abs(fz_total) >= fz_threshold

        # Prefer provided data rate, otherwise estimate
        provided_hz = payload.get("data_rate") or payload.get("dataRate")
        if provided_hz is not None:
            try:
                self.ema_hz = float(provided_hz)
            except Exception:
                pass
        else:
            if self.last_msg_time_ms is not None and time_ms > self.last_msg_time_ms:
                dt_s = (time_ms - self.last_msg_time_ms) / 1000.0
                if dt_s > 0:
                    hz_inst = 1.0 / dt_s
                    self.ema_hz = _ewma(self.ema_hz, hz_inst, 0.2)
        self.last_msg_time_ms = time_ms
        return pos

    def get_snapshot(self) -> Dict[str, Tuple[float, float, float, int, bool]]:
        return {name: ds.snapshot() for name, ds in self.devices.items()}


