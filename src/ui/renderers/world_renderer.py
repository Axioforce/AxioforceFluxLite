from __future__ import annotations
from typing import Tuple, List, Optional, Dict
from PySide6 import QtGui, QtCore

from ... import config
from ...model import LAUNCH_NAME, LANDING_NAME

class WorldRenderer:
    def __init__(self, canvas):
        self.canvas = canvas

    def draw(self, p: QtGui.QPainter) -> None:
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.fillRect(0, 0, self.canvas.width(), self.canvas.height(), QtGui.QColor(*config.COLOR_BG))
        p.setPen(QtGui.QPen(QtGui.QColor(80, 80, 88)))
        p.drawRect(0, 0, max(0, self.canvas.width() - 1), max(0, self.canvas.height() - 1))
        
        if not self.canvas._fit_done and self.canvas.width() > 0 and self.canvas.height() > 0:
            self.canvas._compute_fit()
            
        self._draw_grid(p)
        self._draw_plates(p)
        self.canvas._update_detect_button()
        self.canvas._update_rotate_button()
        
        if self.canvas.state.display_mode == "single":
            if self.canvas._single_snapshot is not None:
                self._draw_cop_single(p, self.canvas._single_snapshot)
        else:
            all_configured = all(self.canvas.state.mound_devices.get(pos) for pos in ["Launch Zone", "Upper Landing Zone", "Lower Landing Zone"])
            if all_configured:
                for name, snap in self.canvas._snapshots.items():
                    if name in (LAUNCH_NAME, LANDING_NAME):
                        self._draw_cop(p, name, snap)
                        
        self._draw_plate_names(p)
        
        # Draw heatmap overlay (single-device view)
        try:
            if self.canvas.state.display_mode == "single" and self.canvas._heatmap_points:
                self._draw_heatmap(p)
        except Exception:
            pass

    def _draw_grid(self, p: QtGui.QPainter) -> None:
        w = self.canvas.width()
        h = self.canvas.height()
        scale = self.canvas.state.px_per_mm
        step = max(12, int(config.GRID_MM_SPACING * scale))
        p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_GRID), 1))
        for x in range(0, w, step):
            p.drawLine(x, 0, x, h)
        for y in range(0, h, step):
            p.drawLine(0, y, w, y)
        base_x, base_y = 12, h - 12
        length = 60
        # Axis indicator: arrows always point right and up on screen.
        # Labels reflect which world axis/sign map to those directions.
        if self.canvas.state.display_mode == "single":
            # Axis labels depend on rotation (arrows fixed right/up)
            # k=0: Right X+, Up Y+; k=1: Right Y+, Up X-; k=2: Right X-, Up Y-; k=3: Right Y-, Up X+
            k = int(self.canvas._rotation_quadrants) % 4
            p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_AXIS_X), config.AXIS_THICKNESS_PX))
            p.drawLine(base_x, base_y, base_x + length, base_y)  # right arrow
            p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_AXIS_Y), config.AXIS_THICKNESS_PX))
            p.drawLine(base_x, base_y, base_x, base_y - length)  # up arrow
            p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_TEXT)))
            if k == 0:
                right_lbl, up_lbl = "X+", "Y+"
            elif k == 1:
                right_lbl, up_lbl = "Y+", "X-"
            elif k == 2:
                right_lbl, up_lbl = "X-", "Y-"
            else:  # k == 3
                right_lbl, up_lbl = "Y-", "X+"
            p.drawText(base_x + length + 6, base_y + 4, right_lbl)
            p.drawText(base_x - 10, base_y - length - 6, up_lbl)
        else:
            # Mound/world view mapping:
            # Screen right => +Y, screen up => -X
            p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_AXIS_Y), config.AXIS_THICKNESS_PX))
            p.drawLine(base_x, base_y, base_x + length, base_y)  # right arrow
            p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_AXIS_X), config.AXIS_THICKNESS_PX))
            p.drawLine(base_x, base_y, base_x, base_y - length)  # up arrow
            p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_TEXT)))
            p.drawText(base_x + length + 6, base_y + 4, "Y+")
            p.drawText(base_x - 10, base_y - length - 6, "X-")

    def _draw_plate(self, p: QtGui.QPainter, center_mm: Tuple[float, float], w_mm: float, h_mm: float) -> None:
        cx, cy = self.canvas._to_screen(center_mm[0], center_mm[1])
        scale = self.canvas.state.px_per_mm
        # For single-view rotation, swap rendered width/height on 90/270
        if self.canvas.state.display_mode == "single" and (self.canvas._rotation_quadrants % 2 == 1):
            w_px = int(h_mm * scale)
            h_px = int(w_mm * scale)
        else:
            w_px = int(w_mm * scale)
            h_px = int(h_mm * scale)
        rect = QtCore.QRect(int(cx - w_px / 2), int(cy - h_px / 2), w_px, h_px)
        p.setBrush(QtGui.QColor(*config.COLOR_PLATE))
        p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_PLATE_OUTLINE), 2))
        p.drawRect(rect)
        if self.canvas.state.flags.show_labels:
            p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_TEXT)))
            label = f"{center_mm} {w_mm:.2f}x{h_mm:.2f}"
            p.drawText(cx + 6, cy - 6, label)
        if self.canvas.state.display_mode == "single" and (self.canvas.state.selected_device_id or "").strip():
            full_id = (self.canvas.state.selected_device_id or "").strip()
            dev_type = (self.canvas.state.selected_device_type or "").strip()
            try:
                if "-" in full_id:
                    prefix, tail = full_id.split("-", 1)
                else:
                    prefix, tail = full_id[:2], full_id
                suffix = tail[-2:] if len(tail) >= 2 else tail
                type_prefix = dev_type if dev_type in ("06", "07", "08", "11") else (prefix if prefix in ("06", "07", "08", "11") else "")
                short = f"{type_prefix}-{suffix}" if type_prefix else suffix
            except Exception:
                short = full_id[-2:] if len(full_id) >= 2 else full_id
            p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_TEXT)))
            top_y = int(cy - h_px / 2) - 26
            p.drawText(int(cx - 100), top_y, 200, 18, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, short)

    def _draw_plate_logo_single(self, p: QtGui.QPainter, center_mm: Tuple[float, float], w_mm: float, h_mm: float, dev_type: str) -> None:
        if self.canvas.state.display_mode != "single":
            return
        # No logo for type 06 plates
        if (dev_type or "").strip() == "06":
            return
        cx, cy = self.canvas._to_screen(center_mm[0], center_mm[1])
        scale = self.canvas.state.px_per_mm
        if (self.canvas._rotation_quadrants % 2 == 1):
            w_px = int(h_mm * scale)
            h_px = int(w_mm * scale)
        else:
            w_px = int(w_mm * scale)
            h_px = int(h_mm * scale)
        left_x = int(cx - w_px / 2)
        right_x = int(cx + w_px / 2)
        top_y = int(cy - h_px / 2)
        bottom_y = int(cy + h_px / 2)
        text = "Axioforce"
        p.save()
        p.setPen(QtGui.QPen(QtGui.QColor(30, 30, 30)))
        font = p.font()
        font.setPointSize(max(9, int(10 * scale / max(scale, 1))))
        p.setFont(font)
        # Determine base side for logo by device type: 07/11 -> left (vertical), 08 -> top (horizontal)
        base_side = "left" if dev_type in ("07", "11") else "top"
        # Apply rotation to pick actual side
        sides = ["top", "right", "bottom", "left"]
        base_idx = sides.index(base_side)
        k = int(self.canvas._rotation_quadrants) % 4
        side = sides[(base_idx + k) % 4]
        inset_px = max(6, int(0.04 * max(w_px, h_px)) + 5)
        if side == "top":
            p.drawText(int(cx - w_px / 2), top_y + 30, w_px, 18, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, text)
        elif side == "bottom":
            p.drawText(int(cx - w_px / 2), bottom_y - 30 - 18, w_px, 18, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, text)
        elif side == "left":
            pivot_x = left_x + inset_px
            pivot_y = int((top_y + bottom_y) / 2)
            p.translate(pivot_x, pivot_y)
            p.rotate(-90)
            p.drawText(-int(h_px / 2), -12, h_px, 24, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, text)
        else:  # right
            pivot_x = right_x - inset_px
            pivot_y = int((top_y + bottom_y) / 2)
            p.translate(pivot_x, pivot_y)
            p.rotate(90)
            p.drawText(-int(h_px / 2), -12, h_px, 24, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, text)
        p.restore()

    def _draw_mound_placeholder(self, p: QtGui.QPainter, center_mm: Tuple[float, float], w_mm: float, h_mm: float, label: str) -> None:
        cx, cy = self.canvas._to_screen(center_mm[0], center_mm[1])
        scale = self.canvas.state.px_per_mm
        w_px = int(w_mm * scale)
        h_px = int(h_mm * scale)
        rect = QtCore.QRect(int(cx - w_px / 2), int(cy - h_px / 2), w_px, h_px)
        grey_fill = QtGui.QColor(80, 80, 80, 150)
        grey_outline = QtGui.QColor(120, 120, 120)
        p.setBrush(grey_fill)
        p.setPen(QtGui.QPen(grey_outline, 2, QtCore.Qt.DashLine))
        p.drawRect(rect)
        text_color = QtGui.QColor(180, 180, 180)
        p.setPen(QtGui.QPen(text_color))
        font = p.font()
        font.setPointSize(10)
        p.setFont(font)
        p.drawText(rect, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, label)

    def _draw_plate_logo_mound(self, p: QtGui.QPainter, center_mm: Tuple[float, float], w_mm: float, h_mm: float) -> None:
        if self.canvas.state.display_mode != "mound":
            return
        cx, cy = self.canvas._to_screen(center_mm[0], center_mm[1])
        scale = self.canvas.state.px_per_mm
        w_px = int(w_mm * scale)
        h_px = int(h_mm * scale)
        right_x = int(cx + w_px / 2)
        top_y = int(cy - h_px / 2)
        bottom_y = int(cy + h_px / 2)
        text = "Axioforce"
        p.save()
        p.setPen(QtGui.QPen(QtGui.QColor(30, 30, 30)))
        font = p.font()
        font.setPointSize(max(9, int(10 * scale / max(scale, 1))))
        p.setFont(font)
        inset_px = max(8, int(0.04 * w_px))
        pivot_x = right_x - inset_px
        pivot_y = int((top_y + bottom_y) / 2)
        p.translate(pivot_x, pivot_y)
        p.rotate(90)
        p.drawText(-int(h_px / 2), -12, h_px, 24, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, text)
        p.restore()

    def _get_plate_dimensions(self, device_id: str) -> Tuple[float, float]:
        if not device_id:
            return config.TYPE07_W_MM, config.TYPE07_H_MM
        for name, axf_id, dev_type in self.canvas._available_devices:
            if axf_id == device_id:
                if dev_type == "06":
                    return config.TYPE06_W_MM, config.TYPE06_H_MM
                elif dev_type == "07":
                    return config.TYPE07_W_MM, config.TYPE07_H_MM
                elif dev_type == "08":
                    return config.TYPE08_W_MM, config.TYPE08_H_MM
                elif dev_type == "11":
                    return config.TYPE11_W_MM, config.TYPE11_H_MM
        return config.TYPE07_W_MM, config.TYPE07_H_MM

    def _draw_plates(self, p: QtGui.QPainter) -> None:
        if not self.canvas.state.flags.show_plates:
            return
        # Logic to determine which plate dimensions to use based on selected device
        if self.canvas.state.display_mode == "single":
            sel_id = (self.canvas.state.selected_device_id or "").strip()
            sel_type = (self.canvas.state.selected_device_type or "").strip()
            
            # If we have a selected ID but no type, try to find it in available devices
            if sel_id and not sel_type:
                 for name, axf_id, dt in self.canvas._available_devices:
                     if axf_id == sel_id:
                         sel_type = dt
                         break
            
            # Fallback if still unknown
            if not sel_type:
                # Default to 07/11 size if uncertain
                w_mm, h_mm = config.TYPE07_W_MM, config.TYPE07_H_MM
            elif sel_type == "06":
                w_mm, h_mm = config.TYPE06_W_MM, config.TYPE06_H_MM
            elif sel_type == "07":
                w_mm, h_mm = config.TYPE07_W_MM, config.TYPE07_H_MM
            elif sel_type == "11":
                w_mm, h_mm = config.TYPE11_W_MM, config.TYPE11_H_MM
            elif sel_type == "08":
                w_mm, h_mm = config.TYPE08_W_MM, config.TYPE08_H_MM
            else:
                w_mm, h_mm = config.TYPE07_W_MM, config.TYPE07_H_MM
                
            self._draw_plate(p, (0.0, 0.0), w_mm, h_mm)
            self._draw_plate_logo_single(p, (0.0, 0.0), w_mm, h_mm, sel_type)
            return

        launch_device = self.canvas.state.mound_devices.get("Launch Zone")
        if launch_device:
            w_mm, h_mm = self._get_plate_dimensions(launch_device)
            self._draw_plate(p, (0.0, 0.0), w_mm, h_mm)
            self._draw_plate_logo_mound(p, (0.0, 0.0), w_mm, h_mm)
        else:
            self._draw_mound_placeholder(p, (0.0, 0.0), config.TYPE07_W_MM, config.TYPE07_H_MM, "Launch Zone\n(Click to select)")
        # Swap: render Upper closest to Launch, Lower farther away
        upper_device = self.canvas.state.mound_devices.get("Upper Landing Zone")
        if upper_device:
            w_mm, h_mm = self._get_plate_dimensions(upper_device)
            self._draw_plate(p, config.LANDING_LOWER_CENTER_MM, w_mm, h_mm)
            self._draw_plate_logo_mound(p, config.LANDING_LOWER_CENTER_MM, w_mm, h_mm)
        else:
            self._draw_mound_placeholder(p, config.LANDING_LOWER_CENTER_MM, config.TYPE08_W_MM, config.TYPE08_H_MM, "Upper Landing\n(Click to select)")
        lower_device = self.canvas.state.mound_devices.get("Lower Landing Zone")
        if lower_device:
            w_mm, h_mm = self._get_plate_dimensions(lower_device)
            self._draw_plate(p, config.LANDING_UPPER_CENTER_MM, w_mm, h_mm)
            self._draw_plate_logo_mound(p, config.LANDING_UPPER_CENTER_MM, w_mm, h_mm)
        else:
            self._draw_mound_placeholder(p, config.LANDING_UPPER_CENTER_MM, config.TYPE08_W_MM, config.TYPE08_H_MM, "Lower Landing\n(Click to select)")

    def _draw_cop(self, p: QtGui.QPainter, name: str, snap: Tuple[float, float, float, int, bool, float, float]) -> None:
        if not self.canvas.state.flags.show_markers:
            return
        x_m, y_m, fz_n, _, is_visible, raw_x_m, raw_y_m = snap
        if not is_visible:
            return

        # Convert m -> mm
        x_mm = self.canvas._scale_cop(x_m)
        y_mm = self.canvas._scale_cop(y_m)
        raw_x_mm = self.canvas._scale_cop(raw_x_m)
        raw_y_mm = self.canvas._scale_cop(raw_y_m)

        color = config.COLOR_COP_LAUNCH if name == LAUNCH_NAME else config.COLOR_COP_LANDING
        cx, cy = self.canvas._to_screen(x_mm, y_mm)
        r_px = max(config.COP_R_MIN_PX, min(config.COP_R_MAX_PX, self.canvas.state.cop_scale_k * abs(fz_n)))
        p.setBrush(QtGui.QColor(*color))
        p.setPen(QtGui.QPen(QtCore.Qt.black, 1))
        p.drawEllipse(QtCore.QPoint(cx, cy), int(r_px), int(r_px))
        p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_TEXT)))
        label = f"{x_mm:.1f}, {y_mm:.1f}"
        p.drawText(cx - 60, int(cy - r_px - 24), 120, 18, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, label)
        zone_cx_mm = 0.0
        zone_cy_mm = 0.0 if name == LAUNCH_NAME else config.LANDING_MID_Y_MM
        zx, zy = self.canvas._to_screen(zone_cx_mm, zone_cy_mm)
        p.drawText(zx - 70, int(zy - self.canvas.state.px_per_mm * (config.TYPE07_H_MM if name == LAUNCH_NAME else config.TYPE08_H_MM) * 0.6),
                   140, 18, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter,
                   f"raw {raw_x_mm:.1f}, {raw_y_mm:.1f}")

    def _draw_cop_single(self, p: QtGui.QPainter, snap: Tuple[float, float, float, int, bool, float, float]) -> None:
        x_m, y_m, fz_n, _, is_visible, raw_x_m, raw_y_m = snap
        if not is_visible:
            return

        # Convert m -> mm
        x_mm = self.canvas._scale_cop(x_m)
        y_mm = self.canvas._scale_cop(y_m)

        cx, cy = self.canvas._to_screen(x_mm, y_mm)
        # In discrete temp testing, when the single-center-circle overlay is active,
        # keep the COP indicator at a fixed pixel size so zoom level and Fz scaling
        # do not change its apparent size on screen.
        try:
            is_discrete_center = bool(self.canvas._grid_overlay.is_center_circle_mode())
        except Exception:
            is_discrete_center = False
        if is_discrete_center:
            base_r = float(getattr(config, "COP_DISCRETE_R_PX", 14.0))
            r_px = max(4.0, base_r)
        else:
            r_px = max(config.COP_R_MIN_PX, min(config.COP_R_MAX_PX, self.canvas.state.cop_scale_k * abs(fz_n)))
        p.setBrush(QtGui.QColor(*config.COLOR_COP_LAUNCH))
        p.setPen(QtGui.QPen(QtCore.Qt.black, 1))
        p.drawEllipse(QtCore.QPoint(cx, cy), int(r_px), int(r_px))
        p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_TEXT)))
        label = f"{x_mm:.1f}, {y_mm:.1f}"
        p.drawText(cx - 60, int(cy - r_px - 24), 120, 18, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, label)

    def _draw_plate_names(self, p: QtGui.QPainter) -> None:
        if self.canvas.state.display_mode == "single":
            return
        self._draw_short_ids_mound(p)

    def _short_id_from_full(self, full_id: str, dev_type_hint: Optional[str] = None) -> str:
        full = (full_id or "").strip()
        if not full:
            return ""
        try:
            if "-" in full:
                prefix, tail = full.split("-", 1)
            else:
                prefix, tail = full[:2], full
            suffix = tail[-2:] if len(tail) >= 2 else tail
            type_prefix = dev_type_hint if dev_type_hint in ("06", "07", "08", "11") else (prefix if prefix in ("06", "07", "08", "11") else "")
            return f"{type_prefix}-{suffix}" if type_prefix else suffix
        except Exception:
            return full[-2:] if len(full) >= 2 else full

    def _draw_short_ids_mound(self, p: QtGui.QPainter) -> None:
        p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_TEXT)))
        scale = self.canvas.state.px_per_mm
        cx, cy = self.canvas._to_screen(0.0, 0.0)
        h_px_launch = int(config.TYPE07_H_MM * scale)
        sid_launch = self._short_id_from_full(self.canvas.state.mound_devices.get("Launch Zone", ""), "07")
        p.drawText(int(cx - 100), int(cy - h_px_launch / 2) - 26, 200, 18, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, sid_launch)
        # Swap label positions: Upper near Launch (lower center), Lower farther (upper center)
        ulx, uly = self.canvas._to_screen(config.LANDING_LOWER_CENTER_MM[0], config.LANDING_LOWER_CENTER_MM[1])
        h_px_l = int(config.TYPE08_H_MM * scale)
        sid_upper = self._short_id_from_full(self.canvas.state.mound_devices.get("Upper Landing Zone", ""), "08")
        p.drawText(int(ulx - 100), int(uly - h_px_l / 2) - 26, 200, 18, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, sid_upper)
        llx, lly = self.canvas._to_screen(config.LANDING_UPPER_CENTER_MM[0], config.LANDING_UPPER_CENTER_MM[1])
        sid_lower = self._short_id_from_full(self.canvas.state.mound_devices.get("Lower Landing Zone", ""), "08")
        p.drawText(int(llx - 100), int(lly - h_px_l / 2) - 26, 200, 18, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, sid_lower)

    def _draw_heatmap(self, p: QtGui.QPainter) -> None:
        # Choose rendering path: enhanced (offscreen compositing) or simple painter stacking
        enhanced = bool(getattr(config, "HEATMAP_ENHANCED_BLEND", False))
        rect = self.canvas._compute_plate_rect_px()
        if rect is None:
            return
        # Common sizing/alpha scaling
        n_pts = max(0, len(self.canvas._heatmap_points))
        try:
            radius_f = 41.6666666667 - (n_pts / 6.0)
        except Exception:
            radius_f = 30.0
        if radius_f < 30.0:
            radius_f = 30.0
        elif radius_f > 40.0:
            radius_f = 40.0
        radius_px = int(radius_f)
        try:
            alpha_center = int(185.0 - 0.5 * n_pts)
        except Exception:
            alpha_center = 170
        if alpha_center < 140:
            alpha_center = 140
        elif alpha_center > 185:
            alpha_center = 185
        if not enhanced:
            # Simple painter-based gradients with normal stacking
            p.save()
            p.setClipRect(rect)
            p.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
            order = ["red", "orange", "yellow", "light_green", "green"]
            groups: Dict[str, List[Tuple[float, float]]] = {k: [] for k in order}
            for x_mm, y_mm, bname in self.canvas._heatmap_points:
                if bname in groups:
                    groups[bname].append((x_mm, y_mm))
                else:
                    groups["red"].append((x_mm, y_mm))
            base_colors = {
                "green": QtGui.QColor(0, 200, 0),
                "light_green": QtGui.QColor(80, 220, 80),
                "yellow": QtGui.QColor(230, 210, 0),
                "orange": QtGui.QColor(230, 140, 0),
                "red": QtGui.QColor(220, 0, 0),
            }
            for key in order:
                pts = groups.get(key, [])
                if not pts:
                    continue
                base = base_colors.get(key, QtGui.QColor(255, 255, 255))
                for x_mm, y_mm in pts:
                    sx, sy = self.canvas._to_screen(x_mm, y_mm)
                    grad = QtGui.QRadialGradient(QtCore.QPointF(sx, sy), float(radius_px))
                    center = QtGui.QColor(base)
                    edge = QtGui.QColor(base)
                    center.setAlpha(int(alpha_center))
                    edge.setAlpha(0)
                    grad.setColorAt(0.0, center)
                    grad.setColorAt(1.0, edge)
                    p.setBrush(QtGui.QBrush(grad))
                    p.setPen(QtCore.Qt.NoPen)
                    p.drawEllipse(QtCore.QPoint(sx, sy), radius_px, radius_px)
            p.restore()
            return
        # Enhanced: offscreen compositing with average alpha and severity-to-color mapping
        w = max(1, rect.width())
        h = max(1, rect.height())
        sum_alpha = [[0.0 for _ in range(w)] for _ in range(h)]
        sum_severity = [[0.0 for _ in range(w)] for _ in range(h)]
        count_overlap = [[0 for _ in range(w)] for _ in range(h)]
        # Union alpha accumulator: a_union = 1 - Π(1 - a_i)
        prod_keep = [[1.0 for _ in range(w)] for _ in range(h)]
        sev_map = {"green": 0.0, "light_green": 0.25, "yellow": 0.5, "orange": 0.75, "red": 1.0}
        for x_mm, y_mm, bname in self.canvas._heatmap_points:
            sx, sy = self.canvas._to_screen(x_mm, y_mm)
            cx = int(sx - rect.left())
            cy = int(sy - rect.top())
            if cx < -radius_px or cy < -radius_px or cx >= w + radius_px or cy >= h + radius_px:
                continue
            severity = float(sev_map.get(bname, 1.0))
            x0 = max(0, cx - radius_px)
            x1 = min(w - 1, cx + radius_px)
            y0 = max(0, cy - radius_px)
            y1 = min(h - 1, cy + radius_px)
            r2 = float(radius_px * radius_px)
            for yy in range(y0, y1 + 1):
                dy = float(yy - cy)
                dy2 = dy * dy
                for xx in range(x0, x1 + 1):
                    dx = float(xx - cx)
                    dist2 = dx * dx + dy2
                    if dist2 > r2:
                        continue
                    d = dist2 ** 0.5
                    a = float(alpha_center) * max(0.0, 1.0 - (d / float(radius_px)))
                    if a <= 0.0:
                        continue
                    sum_alpha[yy][xx] += a
                    sum_severity[yy][xx] += severity * a
                    count_overlap[yy][xx] += 1
                    a_norm = max(0.0, min(1.0, a / 255.0))
                    prod_keep[yy][xx] *= (1.0 - a_norm)
        img = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32)
        img.fill(0)
        for yy in range(h):
            for xx in range(w):
                cnt = count_overlap[yy][xx]
                if cnt <= 0:
                    continue
                a_sum = sum_alpha[yy][xx]
                # Use union alpha so overlaps don't darken
                a_union = 1.0 - max(0.0, min(1.0, prod_keep[yy][xx]))
                aa = int(max(0.0, min(255.0, a_union * 255.0)))
                sev = (sum_severity[yy][xx] / a_sum) if a_sum > 0.0 else 0.0
                sev = max(0.0, min(1.0, sev))
                if sev <= 0.5:
                    t = sev / 0.5
                    r = 0.0 + (230.0 - 0.0) * t
                    g = 200.0 + (210.0 - 200.0) * t
                    b = 0.0
                else:
                    t = (sev - 0.5) / 0.5
                    r = 230.0 + (220.0 - 230.0) * t
                    g = 210.0 + (0.0 - 210.0) * t
                    b = 0.0
                img.setPixel(xx, yy, QtGui.qRgba(int(r), int(g), int(b), int(aa)))
        p.save()
        p.setClipRect(rect)
        p.drawImage(rect.topLeft(), img)
        p.restore()

