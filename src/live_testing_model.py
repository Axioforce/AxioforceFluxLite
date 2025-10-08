from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


GRID_BY_MODEL: Dict[str, Tuple[int, int]] = {
    "06": (3, 3),   # Lite
    "07": (5, 3),   # Launchpad
    "08": (5, 5),   # XL
}


@dataclass
class Thresholds:
    dumbbell_tol_n: float
    bodyweight_tol_n: float


@dataclass
class GridCellResult:
    row: int
    col: int
    fz_mean_n: Optional[float] = None
    cop_x_mm: Optional[float] = None
    cop_y_mm: Optional[float] = None
    error_n: Optional[float] = None
    color_bin: Optional[str] = None  # "green", "light_green", "yellow", "orange", "red"


@dataclass
class LiveTestStage:
    index: int
    name: str  # "45 lb DB", "Body Weight", "Body Weight One Foot"
    location: str  # "A" or "B"
    target_n: float
    total_cells: int
    results: Dict[Tuple[int, int], GridCellResult] = field(default_factory=dict)


@dataclass
class LiveTestSession:
    tester_name: str
    device_id: str
    model_id: str  # "06", "07", "08"
    body_weight_n: float
    thresholds: Thresholds
    grid_rows: int
    grid_cols: int
    stages: List[LiveTestStage] = field(default_factory=list)
    started_at_ms: Optional[int] = None
    ended_at_ms: Optional[int] = None


