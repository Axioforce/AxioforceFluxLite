from __future__ import annotations

from typing import Dict, Tuple

# Re-export from new locations
from .telemetry import DeviceState, Device, _ewma
from .testing import (
    TestThresholds, 
    TestResult, 
    TestStage, 
    TestSession, 
    TemperatureTest
)

# Constants
LAUNCH_NAME = "Launch Zone"
LANDING_NAME = "Landing Zone"

GRID_BY_MODEL: Dict[str, Tuple[int, int]] = {
    "06": (3, 3),   # Lite
    "07": (5, 3),   # Launchpad
    "08": (5, 5),   # XL
    "11": (5, 3),   # Launchpad (identical to 07)
}
