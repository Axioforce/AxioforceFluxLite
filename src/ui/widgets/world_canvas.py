from __future__ import annotations

from typing import Dict, List, Optional, Tuple
import threading

import requests
import json

from PySide6 import QtCore, QtGui, QtWidgets

from ... import config
from ...services.geometry import GeometryService
from ..state import ViewState
from .grid_overlay import GridOverlay
from ..dialogs.device_picker import DevicePickerDialog
from ..renderers.world_renderer import WorldRenderer


class WorldCanvas(QtWidgets.QWidget):
    mound_device_selected = QtCore.Signal(str, str)  # position_id, device_id
    mapping_ready = QtCore.Signal(object)  # Dict[str, str]
    rotation_changed = QtCore.Signal(int)  # quadrants 0..3
    live_cell_clicked = QtCore.Signal(int, int)  # row, col in canonical grid space

    def __init__(self, state: ViewState, parent: Optional[QtWidgets.QWidget] = None) -> None:
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
        self.WORLD_X_MIN, self.WORLD_X_MAX, self.WORLD_Y_MIN, self.WORLD_Y_MAX = \
            GeometryService.compute_world_bounds(self.state.display_mode, self.state.selected_device_type)

    def _compute_fit(self) -> None:
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        self._compute_world_bounds()
        
        bounds = (self.WORLD_X_MIN, self.WORLD_X_MAX, self.WORLD_Y_MIN, self.WORLD_Y_MAX)
        px_per_mm, x_mid, y_mid = GeometryService.compute_fit(w, h, bounds, self.MARGIN_PX)
        
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


