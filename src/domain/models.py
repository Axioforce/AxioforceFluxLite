from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
import time

# --- Constants ---
LAUNCH_NAME = "Launch Zone"
LANDING_NAME = "Landing Zone"

GRID_BY_MODEL: Dict[str, Tuple[int, int]] = {
    "06": (3, 3),   # Lite
    "07": (5, 3),   # Launchpad
    "08": (5, 5),   # XL
    "11": (5, 3),   # Launchpad (identical to 07)
}

# --- Helper Functions ---
def _ewma(prev: Optional[float], new: float, alpha: float) -> float:
    if prev is None:
        return new
    return alpha * new + (1.0 - alpha) * prev

# --- Device Models ---

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
    
    @property
    def grid_size(self) -> Tuple[int, int]:
        return GRID_BY_MODEL.get(self.type, (3, 3))

# --- Test Models ---

@dataclass
class TestThresholds:
    dumbbell_tol_n: float
    bodyweight_tol_n: float

@dataclass
class TestResult:
    row: int
    col: int
    fz_mean_n: Optional[float] = None
    cop_x_mm: Optional[float] = None
    cop_y_mm: Optional[float] = None
    error_n: Optional[float] = None
    color_bin: Optional[str] = None  # "green", "light_green", "yellow", "orange", "red"

@dataclass
class TestStage:
    index: int
    name: str  # "45 lb DB", "Body Weight", "Body Weight One Foot"
    location: str  # "A" or "B"
    target_n: float
    total_cells: int
    results: Dict[Tuple[int, int], TestResult] = field(default_factory=dict)

@dataclass
class TestSession:
    tester_name: str
    device_id: str
    model_id: str  # "06", "07", "08", "11"
    body_weight_n: float
    thresholds: TestThresholds
    grid_rows: int
    grid_cols: int
    stages: List[TestStage] = field(default_factory=list)
    started_at_ms: Optional[int] = None
    ended_at_ms: Optional[int] = None
    is_temp_test: bool = False
    is_discrete_temp: bool = False

    def start(self):
        self.started_at_ms = int(time.time() * 1000)

    def end(self):
        self.ended_at_ms = int(time.time() * 1000)

@dataclass
class TemperatureTest:
    """Represents a discrete temperature test session."""
    id: str
    device_id: str
    timestamp: int
    slopes: Dict[str, float] = field(default_factory=dict)
    raw_data: List[Dict[str, Any]] = field(default_factory=list)
