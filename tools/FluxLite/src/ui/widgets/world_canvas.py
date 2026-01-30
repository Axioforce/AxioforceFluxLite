from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ... import config
from ...app_services.geometry import GeometryService
from ..state import ViewState
from .grid_overlay import GridOverlay
from ..dialogs.device_picker import DevicePickerDialog
from ..renderers.world_renderer import WorldRenderer


class WorldCanvas(QtWidgets.QWidget):
    mound_device_selected = QtCore.Signal(str, str)  # position_id, device_id
    rotation_changed = QtCore.Signal(int)  # quadrants 0..3
    live_cell_clicked = QtCore.Signal(int, int)  # row, col in canonical grid space
    live_background_clicked = QtCore.Signal()  # click outside grid/plate (used to collapse inspectors)
    refresh_devices_clicked = QtCore.Signal()
    tare_clicked = QtCore.Signal()

    def __init__(
        self,
        state: ViewState,
        parent: Optional[QtWidgets.QWidget] = None,
        # Back-compat: older callers provide this kwarg (previously used by the removed
        # "Detect Existing Mound Configuration" feature). Keep it to avoid boot failures.
        backend_address_provider: Any = None,
    ) -> None:
        super().__init__(parent)
        self.state = state
        self._renderer = WorldRenderer(self)
        self._snapshots: Dict[str, Tuple[float, float, float, int, bool, float, float]] = {}
        self._single_snapshot: Optional[Tuple[float, float, float, int, bool, float, float]] = None
        # Prefer a roomy default, but allow the canvas to shrink on smaller screens.
        try:
            self.setMinimumSize(400, 300)
            self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        except Exception:
            # In headless/tests, size hints may not be available; ignore failures here.
            pass
        self.setAutoFillBackground(True)
        # World-coordinate bounds (mm). Pixel margins are adapted per-resize in _compute_fit.
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

        # Single-view rotate button (90° clockwise per click)
        self._rotation_quadrants: int = 0  # 0,1,2,3 => 0°,90°,180°,270° clockwise
        self._rotate_btn = QtWidgets.QPushButton("Rotate 90°", self)
        try:
            self._rotate_btn.setCursor(QtCore.Qt.PointingHandCursor)
        except Exception:
            pass
        self._rotate_btn.setVisible(False)
        self._rotate_btn.clicked.connect(self._on_rotate_clicked)

        # Plate quick actions (bottom-right overlay)
        self._btn_refresh = QtWidgets.QToolButton(self)
        self._btn_refresh.setObjectName("plate_refresh_btn")
        self._btn_tare = QtWidgets.QToolButton(self)
        self._btn_tare.setObjectName("plate_tare_btn")
        self._btn_rotate = QtWidgets.QToolButton(self)
        self._btn_rotate.setObjectName("plate_rotate_btn")
        for b in (self._btn_refresh, self._btn_tare, self._btn_rotate):
            try:
                b.setCursor(QtCore.Qt.PointingHandCursor)
            except Exception:
                pass
            try:
                b.setAutoRaise(True)
            except Exception:
                pass
            try:
                # Text-only glyphs so the "icon" is always white (not OS-themed).
                b.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
            except Exception:
                pass
            try:
                b.setFixedSize(30, 30)
            except Exception:
                pass

        # Refresh icon (SVG) to avoid glyph clipping / font differences.
        self._btn_refresh.setToolTip("Refresh devices")
        self._btn_refresh.clicked.connect(lambda: self.refresh_devices_clicked.emit())
        try:
            from pathlib import Path

            icon_path = Path(__file__).resolve().parent.parent / "assets" / "icons" / "refresh.svg"
            icon = QtGui.QIcon(str(icon_path))
            if not icon.isNull():
                self._btn_refresh.setIcon(icon)
                self._btn_refresh.setIconSize(QtCore.QSize(20, 20))
                self._btn_refresh.setText("")
                self._btn_refresh.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
            else:
                # Fallback if SVG plugin isn't available.
                self._btn_refresh.setText("↻")
                self._btn_refresh.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        except Exception:
            self._btn_refresh.setText("↻")
            try:
                self._btn_refresh.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
            except Exception:
                pass

        self._btn_tare.setText("0.0")
        self._btn_tare.setToolTip("Tare (zero)")
        self._btn_tare.clicked.connect(lambda: self.tare_clicked.emit())

        self._btn_rotate.setText("⟳")
        self._btn_rotate.setToolTip("Rotate plate 90°")
        self._btn_rotate.clicked.connect(self._on_rotate_clicked)

        # Glyph fonts (refresh a bit larger so it reads like an icon)
        try:
            f_refresh = self._btn_refresh.font()
            f_refresh.setPointSize(16)
            f_refresh.setBold(True)
            self._btn_refresh.setFont(f_refresh)

            f_tare = self._btn_tare.font()
            f_tare.setPointSize(11)
            f_tare.setBold(True)
            self._btn_tare.setFont(f_tare)

            f_rot = self._btn_rotate.font()
            f_rot.setPointSize(16)
            f_rot.setBold(True)
            self._btn_rotate.setFont(f_rot)
        except Exception:
            pass

        # Give refresh a bit more room so the glyph can actually appear larger.
        try:
            self._btn_refresh.setFixedSize(38, 38)
        except Exception:
            pass

        # Tare should match refresh button size (but text needs slightly smaller font to avoid eliding).
        try:
            self._btn_tare.setFixedSize(38, 38)
        except Exception:
            pass

        try:
            self._btn_rotate.setFixedSize(38, 38)
        except Exception:
            pass

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: N802
        self._fit_done = False
        super().showEvent(event)
        self.update()
        self._position_rotate_button()
        self._update_rotate_button()
        self._position_plate_action_buttons()

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

    def invalidate_fit(self) -> None:
        """Force recomputing the fit on next paint (used when selection changes)."""
        try:
            self._fit_done = False
        except Exception:
            pass
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
        self._position_rotate_button()
        self._update_rotate_button()
        self._position_plate_action_buttons()

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
                    # Inside overlay bounds but not on the plate rect -> treat as background.
                    self.live_background_clicked.emit()
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
                try:
                    self.live_background_clicked.emit()
                except Exception:
                    pass
        return super().mousePressEvent(event)

    def _compute_world_bounds(self) -> None:
        self.WORLD_X_MIN, self.WORLD_X_MAX, self.WORLD_Y_MIN, self.WORLD_Y_MAX = \
            GeometryService.compute_world_bounds(self.state.display_mode, self.state.selected_device_type)

    def _compute_fit(self) -> None:
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        self._compute_world_bounds()
        
        bounds = (self.WORLD_X_MIN, self.WORLD_X_MAX, self.WORLD_Y_MIN, self.WORLD_Y_MAX)
        px_per_mm, x_mid, y_mid = GeometryService.compute_fit(w, h, bounds, self.MARGIN_PX)

        # In single-device view, size the plate so it takes ~80% of the canvas height.
        # This keeps the default plate view feeling "full" without zooming in/out manually.
        if self.state.display_mode == "single":
            try:
                dev_type = (self.state.selected_device_type or "").strip()
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
                    w_mm = float(config.TYPE08_W_MM)
                    h_mm = float(config.TYPE08_H_MM)

                # Match WorldRenderer's rotation swap: on 90/270, rendered height uses w_mm.
                if int(self._rotation_quadrants) % 2 == 1:
                    plate_h_mm = w_mm
                    plate_w_mm = h_mm
                else:
                    plate_h_mm = h_mm
                    plate_w_mm = w_mm

                # Mirror the adaptive pixel margin used by GeometryService.compute_fit
                base_margin = float(self.MARGIN_PX)
                max_margin = 0.15 * float(min(w, h))
                margin_px = min(base_margin, max_margin)
                margin_px = max(2.0, margin_px)
                usable_w = max(1.0, float(w) - 2.0 * margin_px)
                usable_h = max(1.0, float(h) - 2.0 * margin_px)

                target = float(getattr(config, "PLATE_VIEW_TARGET_HEIGHT_RATIO", 0.80))
                target = max(0.4, min(0.95, target))
                target_w = float(getattr(config, "PLATE_VIEW_TARGET_WIDTH_RATIO", target))
                target_w = max(0.4, min(0.95, target_w))

                # Prefer height-based sizing, but clamp so the plate still fits horizontally.
                s_h = (usable_h * target) / max(1e-6, float(plate_h_mm))
                s_w = (usable_w * target_w) / max(1e-6, float(plate_w_mm))
                px_per_mm = max(0.01, float(min(s_h, s_w)))
            except Exception:
                pass

        self.state.px_per_mm = px_per_mm
        self._x_mid = x_mid
        self._y_mid = y_mid
        self._fit_done = True

    def _to_screen(self, x_mm: float, y_mm: float) -> Tuple[int, int]:
        return GeometryService.world_to_screen(
            x_mm, y_mm, 
            self.width(), self.height(), 
            self.state.px_per_mm, 
            self._x_mid, self._y_mid, 
            self.state.display_mode, 
            self._rotation_quadrants
        )

    def _scale_cop(self, val_m: float) -> float:
        # Convert meters to mm for drawing
        return float(val_m) * 1000.0

    # Rendering is delegated to WorldRenderer (see src/ui/renderers/world_renderer.py)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        p = QtGui.QPainter(self)
        try:
            self._renderer.draw(p)
        finally:
            try:
                p.end()
            except Exception:
                pass

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
            else:
                # Hide overlay if not in single mode or no device selected
                if self._grid_overlay.isVisible():
                    self._grid_overlay.hide()
        except Exception:
            pass
        # Keep overlay buttons visibility in sync with current mode/selection.
        self._update_plate_action_buttons()

    # Public API for live testing overlay
    def show_live_grid(self, rows: int, cols: int) -> None:
        try:
            print(
                "[WorldCanvas] show_live_grid "
                f"rows={rows} cols={cols} "
                f"display_mode={self.state.display_mode} "
                f"selected_device_id={(self.state.selected_device_id or '').strip() or '∅'} "
                f"selected_device_type={(self.state.selected_device_type or '').strip() or '∅'}"
            )
        except Exception:
            pass
        try:
            self._grid_overlay.set_center_circle_mode(False)
        except Exception:
            pass
        self._grid_overlay.set_grid(rows, cols)
        self._grid_overlay.show()
        self.update()

    def hide_live_grid(self) -> None:
        try:
            print("[WorldCanvas] hide_live_grid")
        except Exception:
            pass
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

    def set_live_active_cell(self, row: Optional[int], col: Optional[int]) -> None:
        if row is None or col is None:
            self._grid_overlay.set_active_cell(None, None)
            return
        
        try:
            rows = int(self._grid_overlay.rows)
            cols = int(self._grid_overlay.cols)
        except Exception:
            rows, cols = 0, 0
            
        rr, cc = GeometryService.map_cell(
            int(row), int(col), 
            rows, cols, 
            self._rotation_quadrants, 
            self.state.selected_device_type
        )
        self._grid_overlay.set_active_cell(rr, cc)
        self._grid_overlay.set_active_cell(rr, cc)

    def _map_cell_for_device(self, row: int, col: int) -> Tuple[int, int]:
        try:
            rows = int(self._grid_overlay.rows)
            cols = int(self._grid_overlay.cols)
        except Exception:
            return int(row), int(col)
        if rows <= 0 or cols <= 0:
            return int(row), int(col)
        dev_type = (self.state.selected_device_type or "").strip()
        if dev_type in ("06", "08"):
            r = rows - 1 - int(col)
            c = cols - 1 - int(row)
            return r, c
        return int(row), int(col)

    def _map_cell_for_rotation(self, row: int, col: int) -> Tuple[int, int]:
        try:
            rows = int(self._grid_overlay.rows)
            cols = int(self._grid_overlay.cols)
        except Exception:
            return int(row), int(col)
        if rows <= 0 or cols <= 0:
            return int(row), int(col)
        k = int(self._rotation_quadrants) % 4
        r = int(row)
        c = int(col)
        if k == 0:
            return r, c
        if k == 1:
            return c, (cols - 1 - r)
        if k == 2:
            return (rows - 1 - r), (cols - 1 - c)
        # k == 3
        return (rows - 1 - c), r

    def set_live_cell_color(self, row: int, col: int, color: QtGui.QColor) -> None:
        dr, dc = self._map_cell_for_device(int(row), int(col))
        rr, cc = self._map_cell_for_rotation(dr, dc)
        self._grid_overlay.set_cell_color(rr, cc, color)

    def set_live_cell_text(self, row: int, col: int, text: str) -> None:
        dr, dc = self._map_cell_for_device(int(row), int(col))
        rr, cc = self._map_cell_for_rotation(dr, dc)
        self._grid_overlay.set_cell_text(rr, cc, text)

    def set_live_cell_corner_text(self, row: int, col: int, text: Optional[str]) -> None:
        dr, dc = self._map_cell_for_device(int(row), int(col))
        rr, cc = self._map_cell_for_rotation(dr, dc)
        self._grid_overlay.set_cell_corner_text(rr, cc, text)

    def clear_live_cell_color(self, row: int, col: int) -> None:
        dr, dc = self._map_cell_for_device(int(row), int(col))
        rr, cc = self._map_cell_for_rotation(dr, dc)
        self._grid_overlay.clear_cell_color(rr, cc)

    def set_live_status(self, text: Optional[str]) -> None:
        self._grid_overlay.set_status(text)

    def clear_live_colors(self) -> None:
        self._grid_overlay.clear_colors()
        self._grid_overlay.set_active_cell(None, None)
        self._grid_overlay.set_status(None)

    # --- Calibration heatmap overlay ---
    def set_heatmap_points(self, points: List[Tuple[float, float, str]]) -> None:
        self._heatmap_points = list(points or [])
        self.update()

    def clear_heatmap(self) -> None:
        self._heatmap_points = []
        self.update()

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
        print(f"[canvas] _show_device_picker: position={position_id}, required_type={required_type}, available={len(self._available_devices)} devices")
        for name, axf_id, dev_type in self._available_devices:
            print(f"[canvas]   checking device: name={name}, axf_id={axf_id}, dev_type={dev_type}")
            if dev_type == required_type or (required_type == "07" and dev_type == "11"):
                devices_for_picker.append((name, axf_id, dev_type))
        print(f"[canvas]   filtered to {len(devices_for_picker)} devices for picker")
        dialog = DevicePickerDialog(position_id, required_type, devices_for_picker, self)
        result = dialog.exec()
        if result == QtWidgets.QDialog.Accepted and dialog.selected_device:
            name, axf_id, dev_type = dialog.selected_device
            self.state.mound_devices[position_id] = axf_id
            self.mound_device_selected.emit(position_id, axf_id)
            self.update()

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

    def _position_plate_action_buttons(self) -> None:
        """Bottom-right overlay buttons: Refresh + Tare + Rotate."""
        try:
            margin = 12
            gap = 6
            bw1, bh1 = int(self._btn_refresh.width() or 30), int(self._btn_refresh.height() or 30)
            bw2, bh2 = int(self._btn_tare.width() or 30), int(self._btn_tare.height() or 30)
            bw3, bh3 = int(self._btn_rotate.width() or 30), int(self._btn_rotate.height() or 30)
            bh = max(bh1, bh2, bh3)
            total_w = bw1 + gap + bw2 + gap + bw3
            x0 = max(0, int(self.width() - total_w - margin))
            y0 = max(0, int(self.height() - bh - margin))
            self._btn_refresh.setGeometry(x0, y0 + (bh - bh1), bw1, bh1)
            self._btn_tare.setGeometry(x0 + bw1 + gap, y0 + (bh - bh2), bw2, bh2)
            self._btn_rotate.setGeometry(x0 + bw1 + gap + bw2 + gap, y0 + (bh - bh3), bw3, bh3)
            # Ensure they're above the overlay canvas.
            self._btn_refresh.raise_()
            self._btn_tare.raise_()
            self._btn_rotate.raise_()
        except Exception:
            pass

    def _update_plate_action_buttons(self) -> None:
        """Keep rotate visible; enable only when it makes sense."""
        try:
            is_single = (self.state.display_mode == "single")
            has_device = bool((self.state.selected_device_id or "").strip())
            enabled = bool(is_single and has_device)
            self._btn_rotate.setVisible(True)
            self._btn_rotate.setEnabled(enabled)
        except Exception:
            pass

    def _on_rotate_clicked(self) -> None:
        # Rotate 90° clockwise (visual only).
        try:
            self._rotation_quadrants = (int(self._rotation_quadrants) + 1) % 4
            self._fit_done = False
            self.rotation_changed.emit(int(self._rotation_quadrants))
            self.update()
        except Exception:
            pass

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

    # NOTE: "Detect Existing Mound Configuration" has been removed by design.
