from __future__ import annotations
from typing import Tuple, Optional, Dict, List
import math

from .. import config

class GeometryService:
    """
    Unified service for coordinate transformations, world bounds calculations,
    device specifications, and grid mapping.
    """

    # Grid dimensions (rows, cols) per model id
    GRID_DIMS_BY_MODEL: Dict[str, Tuple[int, int]] = {
        "06": (3, 3),
        "07": (5, 3),
        "08": (5, 5),
        "11": (5, 3),
    }

    @staticmethod
    def get_grid_dimensions(model_id: str) -> Tuple[int, int]:
        """Get the (rows, cols) for a given model ID."""
        return GeometryService.GRID_DIMS_BY_MODEL.get(model_id, (3, 3))

    @staticmethod
    def infer_device_type(meta: Dict[str, object]) -> str:
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

    @staticmethod
    def compute_world_bounds(display_mode: str, selected_device_type: str) -> Tuple[float, float, float, float]:
        """
        Compute the world coordinate bounds (x_min, x_max, y_min, y_max) in mm.
        """
        if display_mode == "single":
            # Auto-zoom the view around the selected plate
            dev_type = (selected_device_type or "").strip()
            if dev_type == "06":
                w_mm = float(config.TYPE06_W_MM)
                h_mm = float(config.TYPE06_H_MM)
            elif dev_type == "07":
                w_mm = float(config.TYPE07_W_MM)
                h_mm = float(config.TYPE07_H_MM)
            elif dev_type == "11":
                w_mm = float(config.TYPE11_W_MM)
                h_mm = float(config.TYPE11_H_MM)
            else:
                # Default to XL plate geometry when device type is unknown/08.
                w_mm = float(config.TYPE08_W_MM)
                h_mm = float(config.TYPE08_H_MM)

            half_w = 0.5 * w_mm
            half_h = 0.5 * h_mm
            longest = max(w_mm, h_mm)
            # Margin is a fixed fraction of plate size so smaller plates are auto-zoomed.
            margin_ratio = float(getattr(config, "PLATE_MARGIN_RATIO", 0.35))
            margin_mm = max(50.0, margin_ratio * longest)
            
            x_min, x_max = -half_h - margin_mm, half_h + margin_mm
            y_min, y_max = -half_w - margin_mm, half_w + margin_mm
            return x_min, x_max, y_min, y_max
            
        else:
            # Dual plate view (Launch/Landing)
            s07_w = config.TYPE07_W_MM / 2.0
            s07_h = config.TYPE07_H_MM / 2.0
            s08_w = config.TYPE08_W_MM / 2.0
            s08_h = config.TYPE08_H_MM / 2.0
            
            x_min = -max(s07_h, s08_h)
            x_max = max(s07_h, s08_h)
            y_edges = [
                -s07_w, s07_w,
                config.LANDING_LOWER_CENTER_MM[1] - s08_w, config.LANDING_LOWER_CENTER_MM[1] + s08_w,
                config.LANDING_UPPER_CENTER_MM[1] - s08_w, config.LANDING_UPPER_CENTER_MM[1] + s08_w,
            ]
            y_min = min(y_edges)
            y_max = max(y_edges)
            margin_mm = 150.0
            
            return x_min - margin_mm, x_max + margin_mm, y_min - margin_mm, y_max + margin_mm

    @staticmethod
    def compute_fit(canvas_w: int, canvas_h: int, world_bounds: Tuple[float, float, float, float], margin_px: float) -> Tuple[float, float, float]:
        """
        Compute scale (px_per_mm) and center offsets to fit world bounds into canvas.
        Returns (px_per_mm, x_mid, y_mid).
        """
        if canvas_w <= 0 or canvas_h <= 0:
            return 1.0, 0.0, 0.0
            
        x_min, x_max, y_min, y_max = world_bounds
        world_w = max(1e-3, float(y_max - y_min))
        world_h = max(1e-3, float(x_max - x_min))
        
        # Adapt the pixel margin
        base_margin = float(margin_px)
        max_margin = 0.15 * float(min(canvas_w, canvas_h))
        margin = min(base_margin, max_margin)
        margin = max(2.0, margin)
        
        usable_w = max(1.0, float(canvas_w) - 2.0 * margin)
        usable_h = max(1.0, float(canvas_h) - 2.0 * margin)
        
        s = min(usable_w / world_w, usable_h / world_h)
        # Clamp to a small but positive value
        px_per_mm = max(0.01, float(s))
        
        y_mid = (y_min + y_max) / 2.0
        x_mid = (x_min + x_max) / 2.0
        
        return px_per_mm, x_mid, y_mid

    @staticmethod
    def apply_rotation(x_mm: float, y_mm: float, quadrants: int) -> Tuple[float, float]:
        """Apply 90-degree rotations to a point."""
        k = int(quadrants) % 4
        if k == 0:
            return x_mm, y_mm
        if k == 1:  # 90° cw
            return y_mm, -x_mm
        if k == 2:  # 180°
            return -x_mm, -y_mm
        # k == 3: 270° cw
        return -y_mm, x_mm

    @staticmethod
    def world_to_screen(x_mm: float, y_mm: float, canvas_w: int, canvas_h: int, 
                       px_per_mm: float, x_mid: float, y_mid: float, 
                       display_mode: str, rotation_quadrants: int) -> Tuple[int, int]:
        """Convert world coordinates (mm) to screen coordinates (px)."""
        cx, cy = canvas_w * 0.5, canvas_h * 0.5
        
        if display_mode == "single":
            rx, ry = GeometryService.apply_rotation(x_mm, y_mm, rotation_quadrants)
            sx = int(cx + (rx - x_mid) * px_per_mm)
            sy = int(cy - (ry - y_mid) * px_per_mm)
        else:
            sx = int(cx + (y_mm - y_mid) * px_per_mm)
            # Flip vertical mapping so X+ renders downward (screen Y increases)
            sy = int(cy + (x_mm - x_mid) * px_per_mm)
            
        return sx, sy

    @staticmethod
    def map_cell(row: int, col: int, rows: int, cols: int, 
                 rotation_quadrants: int, device_type: str) -> Tuple[int, int]:
        """
        Map a logical cell (row, col) to a physical cell index based on device type and rotation.
        Applies device-specific mirroring (e.g. for 06/08) and then rotation.
        """
        # 1. Apply device-specific mirror (Anti-diagonal for 06/08)
        dr, dc = row, col
        dev_type = (device_type or "").strip()
        if dev_type in ("06", "08"):
            # Anti-diagonal mirror: (r, c) -> (rows-1-c, cols-1-r)
            dr = rows - 1 - int(col)
            dc = cols - 1 - int(row)
            
        # 2. Apply rotation
        k = int(rotation_quadrants) % 4
        if k == 0:
            return dr, dc
        if k == 1:  # 90° cw
            return dc, (cols - 1 - dr)
        if k == 2:  # 180°
            return (rows - 1 - dr), (cols - 1 - dc)
        # k == 3: 270° cw
        return (rows - 1 - dc), dr

    @staticmethod
    def map_cop_to_cell(
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

    @staticmethod
    def invert_map_cell(row: int, col: int, rows: int, cols: int, rotation_quadrants: int, device_type: str) -> Tuple[int, int]:
        """
        Invert `map_cell(...)`.

        `map_cell` applies:
        - device-specific mirror (06/08 anti-diagonal)
        - then rotation (0/90/180/270 cw)

        This inverts in reverse order: inverse rotation, then inverse device mirror.
        """
        r = int(row)
        c = int(col)
        rr = int(rows)
        cc = int(cols)

        # 1) Inverse rotation
        k = int(rotation_quadrants) % 4
        if k == 0:
            dr, dc = r, c
        elif k == 1:
            # forward: (r, c) -> (c, cols-1-r)
            dr, dc = (cc - 1 - c), r
        elif k == 2:
            # forward: (r, c) -> (rows-1-r, cols-1-c)
            dr, dc = (rr - 1 - r), (cc - 1 - c)
        else:
            # k == 3, forward: (r, c) -> (rows-1-c, r)
            dr, dc = c, (rr - 1 - r)

        # 2) Inverse device mirror (self-inverse)
        dev_type = (device_type or "").strip()
        if dev_type in ("06", "08"):
            # mirror: (r, c) -> (rows-1-c, cols-1-r) is its own inverse
            return (rr - 1 - int(dc)), (cc - 1 - int(dr))

        return int(dr), int(dc)
