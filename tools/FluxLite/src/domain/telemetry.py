from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple, Any

def _ewma(prev: Optional[float], new: float, alpha: float) -> float:
    if prev is None:
        return new
    return alpha * new + (1.0 - alpha) * prev

@dataclass
class DeviceState:
    """Represents the real-time state of a connected device."""
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
class Device:
    """Represents a physical device configuration."""
    id: str
    type: str  # "06", "07", "08", "11"
    name: str
    config: Dict[str, Any] = field(default_factory=dict)

