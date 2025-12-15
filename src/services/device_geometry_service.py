from __future__ import annotations
from typing import Tuple, Optional, Dict
from .. import config

class DeviceGeometryService:
    """
    Handles geometric calculations and device specifications.
    Source of truth for grid dimensions and physical mapping.
    """

    # Grid dimensions (rows, cols) per model id
    GRID_DIMS_BY_MODEL: Dict[str, Tuple[int, int]] = {
        "06": (3, 3),
        "07": (5, 3),
        "08": (5, 5),
        "11": (5, 3),
    }

    def get_grid_dimensions(self, model_id: str) -> Tuple[int, int]:
        """Get the (rows, cols) for a given model ID."""
        return self.GRID_DIMS_BY_MODEL.get(model_id, (3, 3))

    def infer_device_type(self, meta: Dict[str, object]) -> str:
        """Infer the simplified device type (e.g. '06') from metadata."""
        model = str(meta.get("model_id") or "").strip()
        if model:
            return model[:2]
        device_id = str(meta.get("device_id") or "").strip()
        if device_id:
            # handle formats like '06.0000000c' or '06-...'
            prefix = device_id.split(".", 1)[0]
            prefix = prefix.split("-", 1)[0]
            if prefix:
                return prefix[:2]
        return "06"

    def map_cop_to_cell(
        self,
        device_type: str,
        rows: int,
        cols: int,
        x_mm: Optional[float],
        y_mm: Optional[float],
    ) -> Optional[Tuple[int, int]]:
        """
        Map physical COP coordinates (mm) to a grid cell (row, col).
        Returns None if out of bounds.
        """
        if x_mm is None or y_mm is None:
            return None
        
        dev = (device_type or "").strip()
        
        # Get dimensions from config
        if dev == "07" or dev == "11":
            w_mm = config.TYPE07_W_MM if dev == "07" else config.TYPE11_W_MM
            h_mm = config.TYPE07_H_MM if dev == "07" else config.TYPE11_H_MM
            half_w = w_mm / 2.0
            half_h = h_mm / 2.0
            rx, ry = x_mm, y_mm
        elif dev == "08":
            w_mm = config.TYPE08_W_MM
            h_mm = config.TYPE08_H_MM
            half_w = w_mm / 2.0
            half_h = h_mm / 2.0
            # 08 is rotated 90 deg relative to others in some contexts, 
            # or the axes are swapped. Following original logic:
            rx, ry = y_mm, x_mm
        else:  # default to 06 layout
            w_mm = config.TYPE06_W_MM
            h_mm = config.TYPE06_H_MM
            half_w = w_mm / 2.0
            half_h = h_mm / 2.0
            # 06 logic from original code:
            rx, ry = y_mm, x_mm

        if dev in ("07", "11"):
            if abs(rx) > half_w or abs(ry) > half_h:
                return None
            col_f = (rx + half_w) / w_mm * cols
            row_f = ((half_h - ry) / h_mm) * rows
        else:
            if abs(ry) > half_w or abs(rx) > half_h:
                return None
            col_f = (ry + half_w) / w_mm * cols
            row_f = ((half_h - rx) / h_mm) * rows

        row = min(max(int(row_f), 0), rows - 1)
        col = min(max(int(col_f), 0), cols - 1)
        return (row, col)

