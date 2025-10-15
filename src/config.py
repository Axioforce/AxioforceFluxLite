import os
from dataclasses import dataclass
from typing import Tuple


# Connection defaults
SOCKET_HOST: str = os.environ.get("SOCKET_HOST", "http://localhost")
SOCKET_PORT: int = int(os.environ.get("SOCKET_PORT", "3000"))
HTTP_PORT: int = int(os.environ.get("HTTP_PORT", "3001"))
UI_TICK_HZ: int = int(os.environ.get("UI_TICK_HZ", "100"))


# Microsoft Graph / OneDrive Excel export configuration
# Prefer environment variables; fall back to empty/defaults.
GRAPH_TENANT_ID: str = os.environ.get("GRAPH_TENANT_ID", "")
GRAPH_CLIENT_ID: str = os.environ.get("GRAPH_CLIENT_ID", "")
GRAPH_CLIENT_SECRET: str = os.environ.get("GRAPH_CLIENT_SECRET", "")
# Workbook identification: provide ONE of the following (in order of preference):
# 1) GRAPH_WORKBOOK_SHARING_URL (full OneDrive/SharePoint sharing URL)
# 2) GRAPH_WORKBOOK_ITEM_ID (DriveItem id) with GRAPH_DRIVE_ID optional
# 3) GRAPH_WORKBOOK_PATH (absolute path) with GRAPH_USER_UPN or GRAPH_SITE_PATH
GRAPH_WORKBOOK_SHARING_URL: str = os.environ.get("GRAPH_WORKBOOK_SHARING_URL", "")
GRAPH_WORKBOOK_ITEM_ID: str = os.environ.get("GRAPH_WORKBOOK_ITEM_ID", "")
GRAPH_DRIVE_ID: str = os.environ.get("GRAPH_DRIVE_ID", "")
GRAPH_WORKBOOK_PATH: str = os.environ.get("GRAPH_WORKBOOK_PATH", "")
GRAPH_USER_UPN: str = os.environ.get("GRAPH_USER_UPN", "")
GRAPH_WORKSHEET_NAME: str = os.environ.get("GRAPH_WORKSHEET_NAME", "Summary")


# Drawing and scaling defaults
PX_PER_MM: float = float(os.environ.get("PX_PER_MM", "0.8"))
GRID_MM_SPACING: int = 100
AXIS_THICKNESS_PX: int = 2
ORIGIN_Y_FRACTION: float = float(os.environ.get("ORIGIN_Y_FRACTION", "0.65"))


# Plate footprints (mm) full width x full height (updated real measurements)
# Width corresponds to world Y (right on screen), Height to world X (up on screen)
# 06 plate had width/height reversed previously; corrected here
TYPE06_W_MM: float = 353.2
TYPE06_H_MM: float = 404.0
TYPE07_W_MM: float = 353.3
TYPE07_H_MM: float = 607.3
TYPE08_W_MM: float = 658.1
TYPE08_H_MM: float = 607.3

# Precompute half-extents
TYPE06_HALF_W_MM: float = TYPE06_W_MM / 2.0
TYPE06_HALF_H_MM: float = TYPE06_H_MM / 2.0
TYPE07_HALF_W_MM: float = TYPE07_W_MM / 2.0
TYPE07_HALF_H_MM: float = TYPE07_H_MM / 2.0
TYPE08_HALF_W_MM: float = TYPE08_W_MM / 2.0
TYPE08_HALF_H_MM: float = TYPE08_H_MM / 2.0


# Landing zone placements (mm) - exact centers
LAUNCH_CENTER_MM: Tuple[float, float] = (0.0, 0.0)
# New spec: launch->landing midpoint 1402.6 mm; landing plates have 17 mm gap
LANDING_MID_Y_MM: float = 1402.6
LANDING_OFFSET_Y_MM: float = 337.6  # center-to-plate-center offset along +Y/-Y
LANDING_LOWER_CENTER_MM: Tuple[float, float] = (0.0, LANDING_MID_Y_MM - LANDING_OFFSET_Y_MM)
LANDING_UPPER_CENTER_MM: Tuple[float, float] = (0.0, LANDING_MID_Y_MM + LANDING_OFFSET_Y_MM)


# COP visualization
COP_R_MIN_PX: float = 4.0
COP_R_MAX_PX: float = 40.0
COP_SCALE_K: float = float(os.environ.get("COP_SCALE_K", "0.01"))  # px per Newton
PLOT_SMOOTH_ALPHA: float = float(os.environ.get("PLOT_SMOOTH_ALPHA", "0.1"))  # EMA for force plot lines
OVERLAY_SMOOTH_ALPHA: float = float(os.environ.get("OVERLAY_SMOOTH_ALPHA", "0.1"))  # EMA for overlay numbers (legacy)
OVERLAY_SMOOTH_WINDOW_FRAMES: int = int(os.environ.get("OVERLAY_SMOOTH_WINDOW_FRAMES", "20"))  # Rolling avg frames for overlay numbers

# Data smoothing and noise suppression
FZ_THRESHOLD_N: float = 22.0
SMOOTH_ALPHA: float = 0.2  # EWMA weight


# Live Testing thresholds (N), per model id
# 06 (Lite): DB=5, BW=8 | 07 (Launch): DB=6, BW=11 | 08 (XL): DB=8, BW=15
THRESHOLDS_DB_N_BY_MODEL = {
    "06": 5.0,
    "07": 6.0,
    "08": 8.0,
}

THRESHOLDS_BW_N_BY_MODEL = {
    "06": 8.0,
    "07": 11.0,
    "08": 15.0,
}

# Cell color bins as multipliers of the passing threshold T
# green: ≤0.5T, light-green: (0.5T..T], yellow: (T..1.5T], orange: (1.5T..2.5T], red: >2.5T
COLOR_BIN_MULTIPLIERS = {
    "green": 0.5,
    "light_green": 1.0,
    "yellow": 1.5,
    "orange": 2.5,
}


# Live Testing grid dimensions (rows, cols) per model id
# 06: 3x3, 07: 3x5, 08: 5x5
GRID_DIMS_BY_MODEL = {
    "06": (3, 3),
    "07": (5, 3),
    "08": (5, 5),
}


# Colors as RGB tuples for cross-backend use
COLOR_BG: Tuple[int, int, int] = (18, 18, 20)
COLOR_GRID: Tuple[int, int, int] = (60, 60, 68)
COLOR_AXIS_X: Tuple[int, int, int] = (200, 80, 80)
COLOR_AXIS_Y: Tuple[int, int, int] = (80, 160, 80)
COLOR_PLATE: Tuple[int, int, int] = (90, 110, 140)
COLOR_PLATE_OUTLINE: Tuple[int, int, int] = (180, 200, 230)
COLOR_COP_LAUNCH: Tuple[int, int, int] = (70, 140, 255)
COLOR_COP_LANDING: Tuple[int, int, int] = (80, 210, 120)
COLOR_TEXT: Tuple[int, int, int] = (220, 220, 230)


@dataclass
class UiFlags:
    show_plates: bool = True
    show_markers: bool = True
    show_labels: bool = False



# Automated tare guidance (step-off prompt) configuration
# Interval between step-off prompts while a live session is active
TARE_INTERVAL_S: int = 90
# Required continuous step-off time before auto-tare
TARE_COUNTDOWN_S: int = 15
# Step-off threshold (absolute |Fz| below this counts as off the plate)
TARE_STEP_OFF_THRESHOLD_N: float = 30.0

