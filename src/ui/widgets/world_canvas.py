from __future__ import annotations

from typing import Dict, List, Optional, Tuple
import threading

import requests
import json

from PySide6 import QtCore, QtGui, QtWidgets

from ... import config
from ...model import LAUNCH_NAME, LANDING_NAME
from ..state import ViewState
from .grid_overlay import GridOverlay
from ..dialogs.device_picker import DevicePickerDialog


class WorldCanvas(QtWidgets.QWidget):
    mound_device_selected = QtCore.Signal(str, str)  # position_id, device_id
    mapping_ready = QtCore.Signal(object)  # Dict[str, str]
    rotation_changed = QtCore.Signal(int)  # quadrants 0..3
    live_cell_clicked = QtCore.Signal(int, int)  # row, col in canonical grid space

    def __init__(self, state: ViewState, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.state = state
        self._snapshots: Dict[str, Tuple[float, float, float, int, bool, float, float]] = {}
        self._single_snapshot: Optional[Tuple[float, float, float, int, bool, float, float]] = None
        self.setMinimumSize(800, 600)
        self.setAutoFillBackground(True)
        self.WORLD_X_MIN, self.WORLD_X_MAX = -1.0, 1.0
        self.WORLD_Y_MIN, self.WORLD_Y_MAX = -1.0, 1.0
        self.MARGIN_PX = 20
        self._fit_done = False
        self._x_mid = 0.0
        self._y_mid = 0.0
        self._available_devices: List[Tuple[str, str, str]] = []
        self._active_device_ids: set = set()
        self._heatmap_points: List[Tuple[float, float, str]] = []  # (x_mm, y_mm, bin)

        # Live testing grid overlay
        self._grid_overlay = GridOverlay(self)
        self._grid_overlay.hide()

        # Detect-existing-mound button (visible only in mound mode and until configured)
        self._detect_btn = QtWidgets.QPushButton("Detect Existing Mound Configuration", self)
        try:
            self._detect_btn.setCursor(QtCore.Qt.PointingHandCursor)
        except Exception:
            pass
        self._detect_btn.setVisible(False)
        self._detect_btn.clicked.connect(self._on_detect_clicked)
        self._detect_btn_visible_last: Optional[bool] = None

        # Cross-thread apply for detection results
        try:
            self.mapping_ready.connect(self._on_mapping_ready)
        except Exception:
            pass

        # Single-view rotate button (90° clockwise per click)
        self._rotation_quadrants: int = 0  # 0,1,2,3 => 0°,90°,180°,270° clockwise
        self._rotate_btn = QtWidgets.QPushButton("Rotate 90°", self)
        try:
            self._rotate_btn.setCursor(QtCore.Qt.PointingHandCursor)
        except Exception:
            pass
        self._rotate_btn.setVisible(False)
        self._rotate_btn.clicked.connect(self._on_rotate_clicked)

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: N802
        self._fit_done = False
        super().showEvent(event)
        self.update()
        self._position_detect_button()
        self._update_detect_button()
        self._position_rotate_button()
        self._update_rotate_button()

    def set_snapshots(self, snaps: Dict[str, Tuple[float, float, float, int, bool, float, float]]) -> None:
        self._snapshots = snaps
        sid = (self.state.selected_device_id or "").strip()
        if sid and sid in self._snapshots:
            self._single_snapshot = self._snapshots.get(sid)
        self.update()

    def set_single_snapshot(self, snap: Optional[Tuple[float, float, float, int, bool, float, float]]) -> None:
        self._single_snapshot = snap
        if self.state.display_mode == "single":
            self.update()

    def set_available_devices(self, devices: List[Tuple[str, str, str]]) -> None:
        self._available_devices = devices
        try:
            print(f"[canvas] set_available_devices: count={len(devices)}")
        except Exception:
            pass

    def update_active_devices(self, active_device_ids: set) -> None:
        self._active_device_ids = active_device_ids

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # noqa: N802
        self._fit_done = False
        super().resizeEvent(event)
        self._position_detect_button()
        self._update_detect_button()
        self._position_rotate_button()
        self._update_rotate_button()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if event.button() != QtCore.Qt.LeftButton:
            return super().mousePressEvent(event)
        # Handle clicks differently based on mode
        if self.state.display_mode == "mound":
            pos = event.pos()
            clicked_position = self._get_clicked_position(pos)
            if clicked_position:
                self._show_device_picker(clicked_position)
            return super().mousePressEvent(event)
        # In single-device view, interpret click within overlay grid as a cell click
        if self.state.display_mode == "single" and self._grid_overlay.isVisible():
            pos = event.pos()
            if self._grid_overlay.geometry().contains(pos):
                local = pos - self._grid_overlay.geometry().topLeft()
                # Map local point to overlay cell (rendered coords), then invert to canonical grid cell
                try:
                    rr, cc = self._cell_from_overlay_point(local.x(), local.y())
                    if rr is not None and cc is not None:
                        # Invert rotation/device mapping used when drawing overlay
                        cr, cc2 = self._invert_device_and_rotation(rr, cc)
                        self.live_cell_clicked.emit(int(cr), int(cc2))
                        return
                except Exception:
                    pass
            else:
                # Clicked outside plate/overlay: clear active cell and status
                try:
                    self._grid_overlay.set_active_cell(None, None)
                    self._grid_overlay.set_status(None)
                    self.update()
                except Exception:
                    pass
        return super().mousePressEvent(event)

    def _compute_world_bounds(self) -> None:
        if self.state.display_mode == "single":
            dev_type = (self.state.selected_device_type or "").strip()
            is_07_or_11 = dev_type in ("07", "11")
            half_w = (config.TYPE07_W_MM if is_07_or_11 else config.TYPE08_W_MM) / 2.0
            half_h = (config.TYPE07_H_MM if is_07_or_11 else config.TYPE08_H_MM) / 2.0
            margin_mm = 200.0
            self.WORLD_X_MIN, self.WORLD_X_MAX = -half_h - margin_mm, half_h + margin_mm
            self.WORLD_Y_MIN, self.WORLD_Y_MAX = -half_w - margin_mm, half_w + margin_mm
            return
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
        self.WORLD_X_MIN, self.WORLD_X_MAX = x_min - margin_mm, x_max + margin_mm
        self.WORLD_Y_MIN, self.WORLD_Y_MAX = y_min - margin_mm, y_max + margin_mm

    def _compute_fit(self) -> None:
        w, h = self.width(), self.height()
        self._compute_world_bounds()
        world_w = self.WORLD_Y_MAX - self.WORLD_Y_MIN
        world_h = self.WORLD_X_MAX - self.WORLD_X_MIN
        s = min((w - 2 * self.MARGIN_PX) / world_w, (h - 2 * self.MARGIN_PX) / world_h)
        self.state.px_per_mm = max(0.01, s)
        self._y_mid = (self.WORLD_Y_MIN + self.WORLD_Y_MAX) / 2.0
        self._x_mid = (self.WORLD_X_MIN + self.WORLD_X_MAX) / 2.0
        self._fit_done = True

    def _apply_rotation_single(self, x_mm: float, y_mm: float) -> Tuple[float, float]:
        k = int(self._rotation_quadrants) % 4
        if k == 0:
            return x_mm, y_mm
        if k == 1:  # 90° cw
            return y_mm, -x_mm
        if k == 2:  # 180°
            return -x_mm, -y_mm
        # k == 3: 270° cw
        return -y_mm, x_mm

    def _to_screen(self, x_mm: float, y_mm: float) -> Tuple[int, int]:
        w, h = self.width(), self.height()
        s = self.state.px_per_mm
        assert s > 0
        cx, cy = w * 0.5, h * 0.5
        if self.state.display_mode == "single":
            rx, ry = self._apply_rotation_single(x_mm, y_mm)
            sx = int(cx + (rx - self._x_mid) * s)
            sy = int(cy - (ry - self._y_mid) * s)
        else:
            sx = int(cx + (y_mm - self._y_mid) * s)
            # Flip vertical mapping so X+ renders downward (screen Y increases)
            sy = int(cy + (x_mm - self._x_mid) * s)
        return sx, sy

    def _draw_grid(self, p: QtGui.QPainter) -> None:
        w = self.width()
        h = self.height()
        scale = self.state.px_per_mm
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
        if self.state.display_mode == "single":
            # Axis labels depend on rotation (arrows fixed right/up)
            # k=0: Right X+, Up Y+; k=1: Right Y+, Up X-; k=2: Right X-, Up Y-; k=3: Right Y-, Up X+
            k = int(self._rotation_quadrants) % 4
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
        cx, cy = self._to_screen(center_mm[0], center_mm[1])
        scale = self.state.px_per_mm
        # For single-view rotation, swap rendered width/height on 90/270
        if self.state.display_mode == "single" and (self._rotation_quadrants % 2 == 1):
            w_px = int(h_mm * scale)
            h_px = int(w_mm * scale)
        else:
            w_px = int(w_mm * scale)
            h_px = int(h_mm * scale)
        rect = QtCore.QRect(int(cx - w_px / 2), int(cy - h_px / 2), w_px, h_px)
        p.setBrush(QtGui.QColor(*config.COLOR_PLATE))
        p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_PLATE_OUTLINE), 2))
        p.drawRect(rect)
        if self.state.flags.show_labels:
            p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_TEXT)))
            label = f"{center_mm} {w_mm:.2f}x{h_mm:.2f}"
            p.drawText(cx + 6, cy - 6, label)
        if self.state.display_mode == "single" and (self.state.selected_device_id or "").strip():
            full_id = (self.state.selected_device_id or "").strip()
            dev_type = (self.state.selected_device_type or "").strip()
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
        if self.state.display_mode != "single":
            return
        # No logo for type 06 plates
        if (dev_type or "").strip() == "06":
            return
        cx, cy = self._to_screen(center_mm[0], center_mm[1])
        scale = self.state.px_per_mm
        if (self._rotation_quadrants % 2 == 1):
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
        k = int(self._rotation_quadrants) % 4
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

    def _draw_connection_port_single(self, p: QtGui.QPainter, center_mm: Tuple[float, float], w_mm: float, h_mm: float, dev_type: str) -> None:
        if self.state.display_mode != "single" or dev_type not in ("07", "11", "06"):
            return
        cx, cy = self._to_screen(center_mm[0], center_mm[1])
        scale = self.state.px_per_mm
        if (self._rotation_quadrants % 2 == 1):
            w_px = int(h_mm * scale)
            h_px = int(w_mm * scale)
        else:
            w_px = int(w_mm * scale)
            h_px = int(h_mm * scale)
        port_h_mm = 4.5 * 25.4
        port_w_mm = 2.25 * 25.4
        port_h_px_base = int(port_h_mm * scale)
        port_w_px_base = int(port_w_mm * scale)
        left_x = int(cx - w_px / 2)
        right_x = int(cx + w_px / 2)
        top_y = int(cy - h_px / 2)
        bottom_y = int(cy + h_px / 2)
        inset_px = max(12, int(0.03 * max(w_px, h_px)))
        # Base side is 'right' for connection port; rotate with plate
        sides = ["top", "right", "bottom", "left"]
        base_idx = sides.index("right")
        k = int(self._rotation_quadrants) % 4
        side = sides[(base_idx + k) % 4]
        # Rotate rectangle orientation: vertical on left/right, horizontal on top/bottom
        if side in ("top", "bottom"):
            cur_w = port_h_px_base  # wider horizontally when on top/bottom
            cur_h = port_w_px_base
        else:
            cur_w = port_w_px_base  # taller vertically when on left/right
            cur_h = port_h_px_base
        if side == "right":
            rect_left = right_x - inset_px - cur_w
            rect_top = int(cy - cur_h / 2)
        elif side == "left":
            rect_left = left_x + inset_px
            rect_top = int(cy - cur_h / 2)
        elif side == "top":
            rect_left = int(cx - cur_w / 2)
            rect_top = top_y + inset_px
        else:  # bottom
            rect_left = int(cx - cur_w / 2)
            rect_top = bottom_y - inset_px - cur_h
        rect = QtCore.QRect(rect_left, rect_top, cur_w, cur_h)
        pen = QtGui.QPen(QtGui.QColor(30, 30, 30))
        pen.setStyle(QtCore.Qt.DashLine)
        pen.setWidth(2)
        p.save()
        p.setPen(pen)
        p.setBrush(QtCore.Qt.NoBrush)
        corner_radius = max(6, int(min(cur_w, cur_h) * 0.1))
        p.drawRoundedRect(rect, corner_radius, corner_radius)
        p.restore()

    def _draw_placeholder_plate(self, p: QtGui.QPainter) -> None:
        w_mm = config.TYPE07_H_MM
        h_mm = config.TYPE07_H_MM
        cx, cy = self._to_screen(0.0, 0.0)
        scale = self.state.px_per_mm
        w_px = int(w_mm * scale)
        h_px = int(h_mm * scale)
        rect = QtCore.QRect(int(cx - w_px / 2), int(cy - h_px / 2), w_px, h_px)
        grey_fill = QtGui.QColor(80, 80, 80, 150)
        grey_outline = QtGui.QColor(100, 100, 100)
        p.setBrush(grey_fill)
        p.setPen(QtGui.QPen(grey_outline, 2, QtCore.Qt.DashLine))
        p.drawRect(rect)
        text_color = QtGui.QColor(180, 180, 180)
        p.setPen(QtGui.QPen(text_color))
        font = p.font()
        font.setPointSize(14)
        font.setBold(True)
        p.setFont(font)
        text = "Choose a device below"
        text_rect = QtCore.QRect(int(cx - w_px / 2), int(cy - 20), w_px, 40)
        p.drawText(text_rect, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, text)

    def _draw_mound_placeholder(self, p: QtGui.QPainter, center_mm: Tuple[float, float], w_mm: float, h_mm: float, label: str) -> None:
        cx, cy = self._to_screen(center_mm[0], center_mm[1])
        scale = self.state.px_per_mm
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
        if self.state.display_mode != "mound":
            return
        cx, cy = self._to_screen(center_mm[0], center_mm[1])
        scale = self.state.px_per_mm
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
        for name, axf_id, dev_type in self._available_devices:
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
        if not self.state.flags.show_plates:
            return
        if self.state.display_mode == "single":
            if not (self.state.selected_device_id or "").strip():
                self._draw_placeholder_plate(p)
                return
            dev_type = (self.state.selected_device_type or "").strip()
            if dev_type == "06":
                w_mm = config.TYPE06_W_MM
                h_mm = config.TYPE06_H_MM
            elif dev_type == "07":
                w_mm = config.TYPE07_W_MM
                h_mm = config.TYPE07_H_MM
            elif dev_type == "11":
                w_mm = config.TYPE11_W_MM
                h_mm = config.TYPE11_H_MM
            else:
                w_mm = config.TYPE08_W_MM
                h_mm = config.TYPE08_H_MM
            self._draw_plate(p, (0.0, 0.0), w_mm, h_mm)
            self._draw_plate_logo_single(p, (0.0, 0.0), w_mm, h_mm, dev_type)
            self._draw_connection_port_single(p, (0.0, 0.0), w_mm, h_mm, dev_type)
            return
        launch_device = self.state.mound_devices.get("Launch Zone")
        if launch_device:
            w_mm, h_mm = self._get_plate_dimensions(launch_device)
            self._draw_plate(p, (0.0, 0.0), w_mm, h_mm)
            self._draw_plate_logo_mound(p, (0.0, 0.0), w_mm, h_mm)
        else:
            self._draw_mound_placeholder(p, (0.0, 0.0), config.TYPE07_W_MM, config.TYPE07_H_MM, "Launch Zone\n(Click to select)")
        # Swap: render Upper closest to Launch, Lower farther away
        upper_device = self.state.mound_devices.get("Upper Landing Zone")
        if upper_device:
            w_mm, h_mm = self._get_plate_dimensions(upper_device)
            self._draw_plate(p, config.LANDING_LOWER_CENTER_MM, w_mm, h_mm)
            self._draw_plate_logo_mound(p, config.LANDING_LOWER_CENTER_MM, w_mm, h_mm)
        else:
            self._draw_mound_placeholder(p, config.LANDING_LOWER_CENTER_MM, config.TYPE08_W_MM, config.TYPE08_H_MM, "Upper Landing\n(Click to select)")
        lower_device = self.state.mound_devices.get("Lower Landing Zone")
        if lower_device:
            w_mm, h_mm = self._get_plate_dimensions(lower_device)
            self._draw_plate(p, config.LANDING_UPPER_CENTER_MM, w_mm, h_mm)
            self._draw_plate_logo_mound(p, config.LANDING_UPPER_CENTER_MM, w_mm, h_mm)
        else:
            self._draw_mound_placeholder(p, config.LANDING_UPPER_CENTER_MM, config.TYPE08_W_MM, config.TYPE08_H_MM, "Lower Landing\n(Click to select)")

    def _draw_cop(self, p: QtGui.QPainter, name: str, snap: Tuple[float, float, float, int, bool, float, float]) -> None:
        if not self.state.flags.show_markers:
            return
        x_mm, y_mm, fz_n, _, is_visible, raw_x_mm, raw_y_mm = snap
        if not is_visible:
            return
        color = config.COLOR_COP_LAUNCH if name == LAUNCH_NAME else config.COLOR_COP_LANDING
        cx, cy = self._to_screen(x_mm, y_mm)
        r_px = max(config.COP_R_MIN_PX, min(config.COP_R_MAX_PX, self.state.cop_scale_k * abs(fz_n)))
        p.setBrush(QtGui.QColor(*color))
        p.setPen(QtGui.QPen(QtCore.Qt.black, 1))
        p.drawEllipse(QtCore.QPoint(cx, cy), int(r_px), int(r_px))
        p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_TEXT)))
        label = f"{x_mm:.1f}, {y_mm:.1f}"
        p.drawText(cx - 60, int(cy - r_px - 24), 120, 18, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, label)
        zone_cx_mm = 0.0
        zone_cy_mm = 0.0 if name == LAUNCH_NAME else config.LANDING_MID_Y_MM
        zx, zy = self._to_screen(zone_cx_mm, zone_cy_mm)
        p.drawText(zx - 70, int(zy - self.state.px_per_mm * (config.TYPE07_H_MM if name == LAUNCH_NAME else config.TYPE08_H_MM) * 0.6),
                   140, 18, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter,
                   f"raw {raw_x_mm:.1f}, {raw_y_mm:.1f}")

    def _draw_cop_single(self, p: QtGui.QPainter, snap: Tuple[float, float, float, int, bool, float, float]) -> None:
        x_mm, y_mm, fz_n, _, is_visible, raw_x_mm, raw_y_mm = snap
        if not is_visible:
            return
        cx, cy = self._to_screen(x_mm, y_mm)
        r_px = max(config.COP_R_MIN_PX, min(config.COP_R_MAX_PX, self.state.cop_scale_k * abs(fz_n)))
        p.setBrush(QtGui.QColor(*config.COLOR_COP_LAUNCH))
        p.setPen(QtGui.QPen(QtCore.Qt.black, 1))
        p.drawEllipse(QtCore.QPoint(cx, cy), int(r_px), int(r_px))
        p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_TEXT)))
        label = f"{x_mm:.1f}, {y_mm:.1f}"
        p.drawText(cx - 60, int(cy - r_px - 24), 120, 18, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, label)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.fillRect(0, 0, self.width(), self.height(), QtGui.QColor(*config.COLOR_BG))
        p.setPen(QtGui.QPen(QtGui.QColor(80, 80, 88)))
        p.drawRect(0, 0, max(0, self.width() - 1), max(0, self.height() - 1))
        if not self._fit_done and self.width() > 0 and self.height() > 0:
            self._compute_fit()
        self._draw_grid(p)
        self._draw_plates(p)
        self._update_detect_button()
        self._update_rotate_button()
        if self.state.display_mode == "single":
            if self._single_snapshot is not None:
                self._draw_cop_single(p, self._single_snapshot)
        else:
            all_configured = all(self.state.mound_devices.get(pos) for pos in ["Launch Zone", "Upper Landing Zone", "Lower Landing Zone"])
            if all_configured:
                for name, snap in self._snapshots.items():
                    if name in (LAUNCH_NAME, LANDING_NAME):
                        self._draw_cop(p, name, snap)
        self._draw_plate_names(p)
        # Draw heatmap overlay (single-device view)
        try:
            if self.state.display_mode == "single" and self._heatmap_points:
                self._draw_heatmap(p)
        except Exception:
            pass
        p.end()

        # Resize overlay to plate bounds in single device mode
        try:
            if self.state.display_mode == "single" and (self.state.selected_device_type or "").strip():
                dev_type = (self.state.selected_device_type or "").strip()
                if dev_type == "06":
                    w_mm = config.TYPE06_W_MM
                    h_mm = config.TYPE06_H_MM
                elif dev_type == "07":
                    w_mm = config.TYPE07_W_MM
                    h_mm = config.TYPE07_H_MM
                elif dev_type == "11":
                    w_mm = config.TYPE11_W_MM
                    h_mm = config.TYPE11_H_MM
                else:
                    w_mm = config.TYPE08_W_MM
                    h_mm = config.TYPE08_H_MM
                cx, cy = self._to_screen(0.0, 0.0)
                scale = self.state.px_per_mm
                if (self._rotation_quadrants % 2 == 1):
                    w_px = int(h_mm * scale)
                    h_px = int(w_mm * scale)
                else:
                    w_px = int(w_mm * scale)
                    h_px = int(h_mm * scale)
                rect = QtCore.QRect(int(cx - w_px / 2), int(cy - h_px / 2), w_px, h_px)
                # Enlarge overlay widget to include a side area to the right for status box
                margin = 10
                side_desired = max(260, int(self.width() * 0.25))
                side_avail = max(0, int(self.width() - (rect.right() + margin)))
                side_w = min(side_desired, side_avail)
                ov_w = rect.width() + side_w
                ov_h = rect.height()
                self._grid_overlay.setGeometry(rect.left(), rect.top(), ov_w, ov_h)
                # Plate rect remains at (0,0,w,h) inside the overlay's coordinate space
                self._grid_overlay.set_plate_rect_px(QtCore.QRect(0, 0, rect.width(), rect.height()))
        except Exception:
            pass

    # Public API for live testing overlay
    def show_live_grid(self, rows: int, cols: int) -> None:
        try:
            self._grid_overlay.set_center_circle_mode(False)
        except Exception:
            pass
        self._grid_overlay.set_grid(rows, cols)
        self._grid_overlay.show()
        self.update()

    def hide_live_grid(self) -> None:
        self._grid_overlay.hide()
        self.update()

    def show_live_center_circle(self) -> None:
        """Show single-center-circle overlay used for discrete temperature testing."""
        try:
            self._grid_overlay.set_center_circle_mode(True)
            # 5 cm diameter => 2.5 cm radius => 25 mm; convert to pixels using current scale
            try:
                radius_px = int(25.0 * float(self.state.px_per_mm))
            except Exception:
                radius_px = 0
            self._grid_overlay.set_center_circle_radius_px(radius_px if radius_px > 0 else None)
        except Exception:
            pass
        self._grid_overlay.set_grid(1, 1)
        self._grid_overlay.show()
        self.update()

    def _map_cell_for_rotation(self, row: int, col: int) -> Tuple[int, int]:
        try:
            rows = int(self._grid_overlay.rows)
            cols = int(self._grid_overlay.cols)
        except Exception:
            return int(row), int(col)
        r = int(row)
        c = int(col)
        k = int(self._rotation_quadrants) % 4
        if k == 0:
            return r, c
        if k == 1:  # 90° cw
            return c, (cols - 1 - r)
        if k == 2:  # 180°
            return (rows - 1 - r), (cols - 1 - c)
        # k == 3: 270° cw
        return (rows - 1 - c), r

    def _map_cell_for_device(self, row: int, col: int) -> Tuple[int, int]:
        """Apply device-specific overlay mapping. For 06 and 08, mirror across the
        bottom-left to top-right axis (anti-diagonal). Others: identity.
        """
        try:
            rows = int(self._grid_overlay.rows)
            cols = int(self._grid_overlay.cols)
        except Exception:
            return int(row), int(col)
        dev_type = (self.state.selected_device_type or "").strip()
        if dev_type in ("06", "08"):
            # Anti-diagonal mirror: (r, c) -> (rows-1-c, cols-1-r)
            return (rows - 1 - int(col), cols - 1 - int(row))
        return int(row), int(col)

    def set_live_active_cell(self, row: Optional[int], col: Optional[int]) -> None:
        if row is None or col is None:
            self._grid_overlay.set_active_cell(None, None)
            return
        # Apply device-specific mirror first (e.g., 06/08), then rotation mapping
        dr, dc = self._map_cell_for_device(int(row), int(col))
        rr, cc = self._map_cell_for_rotation(dr, dc)
        self._grid_overlay.set_active_cell(rr, cc)

    def set_live_cell_color(self, row: int, col: int, color: QtGui.QColor) -> None:
        dr, dc = self._map_cell_for_device(int(row), int(col))
        rr, cc = self._map_cell_for_rotation(dr, dc)
        self._grid_overlay.set_cell_color(rr, cc, color)

    def clear_live_cell_color(self, row: int, col: int) -> None:
        dr, dc = self._map_cell_for_device(int(row), int(col))
        rr, cc = self._map_cell_for_rotation(dr, dc)
        self._grid_overlay.clear_cell_color(rr, cc)

    def set_live_status(self, text: Optional[str]) -> None:
        self._grid_overlay.set_status(text)

    def clear_live_colors(self) -> None:
        self._grid_overlay.clear_colors()

    # --- Calibration heatmap overlay ---
    def set_heatmap_points(self, points: List[Tuple[float, float, str]]) -> None:
        self._heatmap_points = list(points or [])
        self.update()

    def clear_heatmap(self) -> None:
        self._heatmap_points = []
        self.update()

    def _draw_heatmap(self, p: QtGui.QPainter) -> None:
        # Choose rendering path: enhanced (offscreen compositing) or simple painter stacking
        enhanced = bool(getattr(config, "HEATMAP_ENHANCED_BLEND", False))
        rect = self._compute_plate_rect_px()
        if rect is None:
            return
        # Common sizing/alpha scaling
        n_pts = max(0, len(self._heatmap_points))
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
            for x_mm, y_mm, bname in self._heatmap_points:
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
                    sx, sy = self._to_screen(x_mm, y_mm)
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
        for x_mm, y_mm, bname in self._heatmap_points:
            sx, sy = self._to_screen(x_mm, y_mm)
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

    def _compute_plate_rect_px(self) -> Optional[QtCore.QRect]:
        try:
            if self.state.display_mode != "single" or not (self.state.selected_device_type or "").strip():
                return None
            dev_type = (self.state.selected_device_type or "").strip()
            if dev_type == "06":
                w_mm = config.TYPE06_W_MM
                h_mm = config.TYPE06_H_MM
            elif dev_type == "07":
                w_mm = config.TYPE07_W_MM
                h_mm = config.TYPE07_H_MM
            elif dev_type == "11":
                w_mm = config.TYPE11_W_MM
                h_mm = config.TYPE11_H_MM
            else:
                w_mm = config.TYPE08_W_MM
                h_mm = config.TYPE08_H_MM
            cx, cy = self._to_screen(0.0, 0.0)
            scale = self.state.px_per_mm
            if (self._rotation_quadrants % 2 == 1):
                w_px = int(h_mm * scale)
                h_px = int(w_mm * scale)
            else:
                w_px = int(w_mm * scale)
                h_px = int(h_mm * scale)
            return QtCore.QRect(int(cx - w_px / 2), int(cy - h_px / 2), w_px, h_px)
        except Exception:
            return None

    # Expose rotation for live-testing mapping
    def get_rotation_quadrants(self) -> int:
        return int(self._rotation_quadrants) % 4

    def rotate_coords_for_mapping(self, x_mm: float, y_mm: float) -> Tuple[float, float]:
        return self._apply_rotation_single(x_mm, y_mm)

    def _cell_from_overlay_point(self, x_px: int, y_px: int) -> Tuple[Optional[int], Optional[int]]:
        try:
            rect = self._grid_overlay._plate_rect_px  # noqa: SLF001
            rows = int(self._grid_overlay.rows)
            cols = int(self._grid_overlay.cols)
            if rect is None or rows <= 0 or cols <= 0:
                return None, None
            if x_px < rect.left() or x_px > rect.right() or y_px < rect.top() or y_px > rect.bottom():
                return None, None
            cell_w = rect.width() / max(1, cols)
            cell_h = rect.height() / max(1, rows)
            c = int((x_px - rect.left()) / cell_w)
            r = int((y_px - rect.top()) / cell_h)
            c = max(0, min(cols - 1, c))
            r = max(0, min(rows - 1, r))
            return r, c
        except Exception:
            return None, None

    def _invert_rotation_mapping(self, row: int, col: int) -> Tuple[int, int]:
        try:
            rows = int(self._grid_overlay.rows)
            cols = int(self._grid_overlay.cols)
        except Exception:
            return int(row), int(col)
        k = int(self._rotation_quadrants) % 4
        r = int(row)
        c = int(col)
        # Inverse of _map_cell_for_rotation
        if k == 0:
            return r, c
        if k == 1:  # previous mapping: (r, c) -> (c, cols-1-r)
            return (cols - 1 - c), r
        if k == 2:  # previous: (r, c) -> (rows-1-r, cols-1-c)
            return (rows - 1 - r), (cols - 1 - c)
        # k == 3: previous: (r, c) -> (rows-1-c, r)
        return c, (rows - 1 - r)

    def _invert_device_mapping(self, row: int, col: int) -> Tuple[int, int]:
        try:
            rows = int(self._grid_overlay.rows)
            cols = int(self._grid_overlay.cols)
        except Exception:
            return int(row), int(col)
        dev_type = (self.state.selected_device_type or "").strip()
        if dev_type in ("06", "08"):
            # Inverse of anti-diagonal mirror is itself
            return (rows - 1 - int(col)), (cols - 1 - int(row))
        return int(row), int(col)

    def _invert_device_and_rotation(self, row: int, col: int) -> Tuple[int, int]:
        # Inverse order of application: rotation first (inverse), then device (inverse)
        rr, cc = self._invert_rotation_mapping(int(row), int(col))
        return self._invert_device_mapping(rr, cc)

    def _draw_plate_names(self, p: QtGui.QPainter) -> None:
        return

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
        scale = self.state.px_per_mm
        cx, cy = self._to_screen(0.0, 0.0)
        h_px_launch = int(config.TYPE07_H_MM * scale)
        sid_launch = self._short_id_from_full(self.state.mound_devices.get("Launch Zone", ""), "07")
        p.drawText(int(cx - 100), int(cy - h_px_launch / 2) - 26, 200, 18, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, sid_launch)
        # Swap label positions: Upper near Launch (lower center), Lower farther (upper center)
        ulx, uly = self._to_screen(config.LANDING_LOWER_CENTER_MM[0], config.LANDING_LOWER_CENTER_MM[1])
        h_px_l = int(config.TYPE08_H_MM * scale)
        sid_upper = self._short_id_from_full(self.state.mound_devices.get("Upper Landing Zone", ""), "08")
        p.drawText(int(ulx - 100), int(uly - h_px_l / 2) - 26, 200, 18, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, sid_upper)
        llx, lly = self._to_screen(config.LANDING_UPPER_CENTER_MM[0], config.LANDING_UPPER_CENTER_MM[1])
        sid_lower = self._short_id_from_full(self.state.mound_devices.get("Lower Landing Zone", ""), "08")
        p.drawText(int(llx - 100), int(lly - h_px_l / 2) - 26, 200, 18, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, sid_lower)

    def _get_clicked_position(self, pos: QtCore.QPoint) -> Optional[str]:
        if not self._fit_done:
            return None
        scale = self.state.px_per_mm
        cx, cy = self._to_screen(0.0, 0.0)
        w_px = int(config.TYPE07_W_MM * scale)
        h_px = int(config.TYPE07_H_MM * scale)
        rect = QtCore.QRect(int(cx - w_px / 2), int(cy - h_px / 2), w_px, h_px)
        label_rect = QtCore.QRect(int(cx - 100), int(cy - h_px / 2) - 26, 200, 18)
        if rect.contains(pos) or label_rect.contains(pos):
            return "Launch Zone"
        # Swap click targets: Upper near Launch (lower center), Lower farther (upper center)
        ulx, uly = self._to_screen(config.LANDING_LOWER_CENTER_MM[0], config.LANDING_LOWER_CENTER_MM[1])
        w_px_l = int(config.TYPE08_W_MM * scale)
        h_px_l = int(config.TYPE08_H_MM * scale)
        rect = QtCore.QRect(int(ulx - w_px_l / 2), int(uly - h_px_l / 2), w_px_l, h_px_l)
        label_rect = QtCore.QRect(int(ulx - 100), int(uly - h_px_l / 2) - 26, 200, 18)
        if rect.contains(pos) or label_rect.contains(pos):
            return "Upper Landing Zone"
        llx, lly = self._to_screen(config.LANDING_UPPER_CENTER_MM[0], config.LANDING_UPPER_CENTER_MM[1])
        rect = QtCore.QRect(int(llx - w_px_l / 2), int(lly - h_px_l / 2), w_px_l, h_px_l)
        label_rect = QtCore.QRect(int(llx - 100), int(lly - h_px_l / 2) - 26, 200, 18)
        if rect.contains(pos) or label_rect.contains(pos):
            return "Lower Landing Zone"
        return None

    def _show_device_picker(self, position_id: str) -> None:
        if position_id == "Launch Zone":
            required_type = "07"  # Also accepts "11" - see filtering logic below
        else:
            required_type = "08"
        devices_for_picker: List[Tuple[str, str, str]] = []
        for name, axf_id, dev_type in self._available_devices:
            if dev_type == required_type or (required_type == "07" and dev_type == "11"):
                devices_for_picker.append((name, axf_id, dev_type))
        dialog = DevicePickerDialog(position_id, required_type, devices_for_picker, self)
        result = dialog.exec()
        if result == QtWidgets.QDialog.Accepted and dialog.selected_device:
            name, axf_id, dev_type = dialog.selected_device
            self.state.mound_devices[position_id] = axf_id
            self.mound_device_selected.emit(position_id, axf_id)
            self.update()
            self._update_detect_button()

    # --- Detect existing mound configuration (HTTP to backend) ---
    def _position_detect_button(self) -> None:
        try:
            hint = self._detect_btn.sizeHint()
            w = min(max(220, hint.width() + 20), max(260, int(self.width() * 0.7)))
            h = max(26, hint.height() + 6)
            x = int((self.width() - w) / 2)
            y = 8  # top padding
            self._detect_btn.setGeometry(x, y, w, h)
        except Exception:
            pass

    def _update_detect_button(self) -> None:
        try:
            is_mound = (self.state.display_mode == "mound")
            all_configured = all(self.state.mound_devices.get(pos) for pos in ["Launch Zone", "Upper Landing Zone", "Lower Landing Zone"])
            visible = bool(is_mound and not all_configured)
            if self._detect_btn_visible_last is None or self._detect_btn_visible_last != visible:
                self._detect_btn_visible_last = visible
                print(f"[canvas] detect button visible -> {visible} (is_mound={is_mound}, configured={all_configured}, mound_devices={self.state.mound_devices})")
            self._detect_btn.setVisible(visible)
        except Exception:
            pass

    def _position_rotate_button(self) -> None:
        try:
            hint = self._rotate_btn.sizeHint()
            w = max(110, hint.width() + 12)
            h = max(26, hint.height() + 6)
            margin = 10
            x = max(0, self.width() - w - margin)
            y = max(0, self.height() - h - margin)
            self._rotate_btn.setGeometry(x, y, w, h)
        except Exception:
            pass

    def _update_rotate_button(self) -> None:
        try:
            # Disable rotation UI for now
            self._rotate_btn.setVisible(False)
        except Exception:
            pass

    def _on_rotate_clicked(self) -> None:
        # Rotation disabled — ignore clicks
        return

    # Allow external sync of rotation state (e.g., from sibling canvas)
    def set_rotation_quadrants(self, k: int) -> None:
        try:
            k_norm = int(k) % 4
            if k_norm == int(self._rotation_quadrants) % 4:
                return
            self._rotation_quadrants = k_norm
            self._fit_done = False
            self.update()
        except Exception:
            pass

    def _http_base(self) -> str:
        base = str(getattr(config, "SOCKET_HOST", "http://localhost") or "http://localhost")
        port = int(getattr(config, "HTTP_PORT", 3001))
        base = base.rstrip("/")
        if ":" in base.split("//", 1)[-1]:
            # Host already has a port; replace with HTTP_PORT
            try:
                head, tail = base.split("://", 1)
                host_only = tail.split(":")[0]
                base = f"{head}://{host_only}:{port}"
            except Exception:
                base = f"{base}:{port}"
        else:
            base = f"{base}:{port}"
        try:
            print(f"[canvas] http base resolved: {base}")
        except Exception:
            pass
        return base

    def _on_detect_clicked(self) -> None:
        print("[canvas] detect clicked")
        self._detect_btn.setEnabled(False)
        t = threading.Thread(target=self._detect_worker, daemon=True)
        t.start()

    def _detect_worker(self) -> None:
        base = self._http_base()
        mapping: Dict[str, str] = {}
        try:
            # Prefer groups for explicit configuration label
            url_g = f"{base}/api/get-groups"
            print(f"[canvas] GET {url_g}")
            resp = requests.get(url_g, timeout=4)
            if resp.ok:
                payload = resp.json() or {}
                try:
                    print(f"[canvas] get-groups ok: keys={list(payload.keys())}")
                except Exception:
                    pass
                groups = payload.get("response") or payload.get("groups") or []
                print(f"[canvas] get-groups: groups_count={len(groups)}")
                for g in groups:
                    cfg = str(g.get("group_configuration") or g.get("configuration") or "").lower()
                    try:
                        print(f"[canvas] group cfg={cfg} name={g.get('name')} id={g.get('axf_id') or g.get('axfId')}")
                    except Exception:
                        pass
                    if "pitching" in cfg and "mound" in cfg:
                        # Extract devices
                        for d in (g.get("devices") or []):
                            # Accept both key styles
                            name = str(d.get("name") or d.get("plateName") or "").strip()
                            device_id = str(d.get("axf_id") or d.get("deviceId") or "").strip()
                            pos_id = str(d.get("position_id") or d.get("positionId") or name).strip()
                            is_virtual = bool(d.get("is_virtual"))
                            print(f"[canvas] groups device: name={name} pos_id={pos_id} id={device_id} virtual={is_virtual}")
                            if pos_id in ("Upper Landing Zone", "Lower Landing Zone") and not is_virtual:
                                mapping[pos_id] = device_id
                            if pos_id == "Launch Zone" and not is_virtual:
                                mapping["Launch Zone"] = device_id
                        break
            else:
                print(f"[canvas] get-groups failed: status={resp.status_code} body={str(resp.text)[:200]}")
        except Exception as e:
            print(f"[canvas] get-groups error: {e}")
        # Emit to UI thread to apply mapping immediately
        try:
            print(f"[canvas] emitting mapping_ready with: {mapping}")
            self.mapping_ready.emit(mapping)
        except Exception as ee:
            print(f"[canvas] mapping emit failed: {ee}")

    def _on_mapping_ready(self, mapping: Dict[str, str]) -> None:
        try:
            # Only set fields we found; do not emit selection signals (no group create/update)
            changed = False
            print(f"[canvas] applying mapping on UI thread: {mapping}")
            for key in ("Launch Zone", "Upper Landing Zone", "Lower Landing Zone"):
                val = mapping.get(key)
                if val:
                    if self.state.mound_devices.get(key) != val:
                        self.state.mound_devices[key] = val
                        changed = True
            if changed:
                print(f"[canvas] mound_devices updated: {self.state.mound_devices}")
                self.update()
            else:
                print("[canvas] no changes to mound_devices")
        except Exception as e:
            print(f"[canvas] mapping apply error: {e}")
        finally:
            try:
                self._detect_btn.setEnabled(True)
                self._update_detect_button()
            except Exception:
                pass


