from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
import time

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

