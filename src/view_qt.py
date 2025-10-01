from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Tuple, List

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except Exception as e:  # pragma: no cover - import checked by main
    raise

from . import config
from .model import LAUNCH_NAME, LANDING_NAME


class UiBridge(QtCore.QObject):
    """Thread-safe bridge: controller threads emit signals; UI updates happen on the main thread."""
    snapshots_ready = QtCore.Signal(object, object)  # snaps: Dict[str, tuple], hz_text: Optional[str]
    connection_text_ready = QtCore.Signal(str)
    single_snapshot_ready = QtCore.Signal(object)  # Optional[tuple]
    plate_device_id_ready = QtCore.Signal(str, str)  # plate_name, device_id
    available_devices_ready = QtCore.Signal(object)  # List[Tuple[str, str, str]]
    active_devices_ready = QtCore.Signal(object)  # set[str]
    # Real-time force data: (device_id, time_ms, fx, fy, fz)
    force_vector_ready = QtCore.Signal(str, int, float, float, float)


class DeviceListDelegate(QtWidgets.QStyledItemDelegate):
    """Custom delegate to render green checkmark for active devices."""
    
    def paint(self, painter: QtGui.QPainter, option: QtWidgets.QStyleOptionViewItem, index: QtCore.QModelIndex) -> None:
        # Draw the default item
        super().paint(painter, option, index)
        
        # Check if this device is active (stored in custom role)
        is_active = index.data(QtCore.Qt.UserRole + 1)
        if is_active:
            # Draw green checkmark next to the text
            painter.save()
            rect = option.rect
            text = index.data(QtCore.Qt.DisplayRole)
            check_text = " ✓"
            painter.setFont(option.font)
            fm = painter.fontMetrics()
            
            # Calculate position right after the text
            text_width = fm.horizontalAdvance(text)
            x = rect.left() + text_width + 5  # 5px padding from text
            y = rect.center().y() + fm.ascent() // 2
            
            # Draw green checkmark
            painter.setPen(QtGui.QColor(100, 200, 100))  # Green color
            painter.drawText(x, y, check_text)
            painter.restore()


class DevicePickerDialog(QtWidgets.QDialog):
    """Dialog for selecting a device for a mound position."""
    
    def __init__(self, position_name: str, device_type: str, available_devices: List[Tuple[str, str, str]], parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.selected_device: Optional[Tuple[str, str, str]] = None
        
        self.setWindowTitle(f"Select Device for {position_name}")
        self.setModal(True)
        self.setMinimumWidth(400)
        self.setMinimumHeight(300)
        
        layout = QtWidgets.QVBoxLayout(self)
        
        # Info label
        label = QtWidgets.QLabel(f"Select a Type {device_type} device for {position_name}:")
        layout.addWidget(label)
        
        # Device list
        self.device_list = QtWidgets.QListWidget()
        self.device_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        
        # Filter and populate
        for name, axf_id, dev_type in available_devices:
            if dev_type == device_type:
                display = f"{name} ({axf_id})"
                item = QtWidgets.QListWidgetItem(display)
                item.setData(QtCore.Qt.UserRole, (name, axf_id, dev_type))
                self.device_list.addItem(item)
        
        layout.addWidget(self.device_list)
        
        # Buttons
        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        
        # Double-click to select
        self.device_list.itemDoubleClicked.connect(self._on_accept)
    
    def _on_accept(self) -> None:
        current = self.device_list.currentItem()
        if current:
            self.selected_device = current.data(QtCore.Qt.UserRole)
            self.accept()


@dataclass
class ViewState:
    px_per_mm: float = config.PX_PER_MM
    cop_scale_k: float = config.COP_SCALE_K
    flags: config.UiFlags = field(default_factory=config.UiFlags)
    connection_text: str = "Disconnected"
    data_rate_text: str = "Hz: --"
    # Display configuration
    display_mode: str = "mound"  # "mound" or "single"
    selected_device_id: Optional[str] = None  # axfId / full device id
    selected_device_type: Optional[str] = None  # "06", "07" or "08"
    selected_device_name: Optional[str] = None  # human-friendly name
    plate_device_ids: Dict[str, str] = field(default_factory=dict)  # map LAUNCH_NAME/LANDING_NAME to full device id
    # Pitching mound device assignments
    mound_devices: Dict[str, Optional[str]] = field(default_factory=lambda: {
        "Launch Zone": None,
        "Upper Landing Zone": None,
        "Lower Landing Zone": None
    })  # position_id -> device_id
    # UI layout options


class WorldCanvas(QtWidgets.QWidget):
    mound_device_selected = QtCore.Signal(str, str)  # position_id, device_id
    
    def __init__(self, state: ViewState, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.state = state
        self._snapshots: Dict[str, Tuple[float, float, float, int, bool, float, float]] = {}
        self._single_snapshot: Optional[Tuple[float, float, float, int, bool, float, float]] = None
        self.setMinimumSize(800, 600)
        self.setAutoFillBackground(True)
        # World bounds for auto-fit (mm)
        self.WORLD_X_MIN, self.WORLD_X_MAX = -1.0, 1.0             # lateral X (placeholder; computed)
        self.WORLD_Y_MIN, self.WORLD_Y_MAX = -1.0, 1.0             # forward Y (placeholder; computed)
        self.MARGIN_PX = 20
        self._fit_done = False
        self._x_mid = 0.0
        self._y_mid = 0.0
        self._available_devices: List[Tuple[str, str, str]] = []  # For device picker
        self._active_device_ids: set = set()  # Active devices sending data

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: N802 (Qt naming)
        # Ensure we compute an initial fit once the widget is actually visible
        self._fit_done = False
        super().showEvent(event)
        self.update()

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

    def update_active_devices(self, active_device_ids: set) -> None:
        self._active_device_ids = active_device_ids

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # noqa: N802 (Qt naming)
        # Invalidate fit so we recompute with the new size
        self._fit_done = False
        super().resizeEvent(event)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802 (Qt naming)
        """Handle clicks on plates and labels in mound mode."""
        if self.state.display_mode != "mound" or event.button() != QtCore.Qt.LeftButton:
            return super().mousePressEvent(event)
        pos = event.pos()
        clicked_position = self._get_clicked_position(pos)
        if clicked_position:
            self._show_device_picker(clicked_position)
        super().mousePressEvent(event)

    def _compute_world_bounds(self) -> None:
        if self.state.display_mode == "single":
            is_07 = (self.state.selected_device_type or "").strip() == "07"
            half_w = (config.TYPE07_W_MM if is_07 else config.TYPE08_W_MM) / 2.0
            half_h = (config.TYPE07_H_MM if is_07 else config.TYPE08_H_MM) / 2.0
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

    def _to_screen(self, x_mm: float, y_mm: float) -> Tuple[int, int]:
        w, h = self.width(), self.height()
        s = self.state.px_per_mm
        assert s > 0
        cx, cy = w * 0.5, h * 0.5
        if self.state.display_mode == "single":
            sx = int(cx + (x_mm - self._x_mid) * s)
            sy = int(cy - (y_mm - self._y_mid) * s)
        else:
            sx = int(cx + (y_mm - self._y_mid) * s)
            sy = int(cy - (x_mm - self._x_mid) * s)
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
        if self.state.display_mode == "single":
            p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_AXIS_X), config.AXIS_THICKNESS_PX))
            p.drawLine(base_x, base_y, base_x + length, base_y)
            p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_AXIS_Y), config.AXIS_THICKNESS_PX))
            p.drawLine(base_x, base_y, base_x, base_y - length)
            p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_TEXT)))
            p.drawText(base_x + length + 6, base_y + 4, "X")
            p.drawText(base_x - 10, base_y - length - 6, "Y")
        else:
            p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_AXIS_Y), config.AXIS_THICKNESS_PX))
            p.drawLine(base_x, base_y, base_x + length, base_y)
            p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_AXIS_X), config.AXIS_THICKNESS_PX))
            p.drawLine(base_x, base_y, base_x, base_y - length)
            p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_TEXT)))
            p.drawText(base_x + length + 6, base_y + 4, "Y+")
            p.drawText(base_x - 10, base_y - length - 6, "X+")

    def _draw_plate(self, p: QtGui.QPainter, center_mm: Tuple[float, float], w_mm: float, h_mm: float) -> None:
        cx, cy = self._to_screen(center_mm[0], center_mm[1])
        scale = self.state.px_per_mm
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
        # In single-device mode, annotate plate with shortened device id at the top
        if self.state.display_mode == "single" and (self.state.selected_device_id or "").strip():
            full_id = (self.state.selected_device_id or "").strip()
            dev_type = (self.state.selected_device_type or "").strip()
            # Build short id: type (07/08) followed by '-' and last two chars of unique id
            try:
                if "-" in full_id:
                    prefix, tail = full_id.split("-", 1)
                else:
                    prefix, tail = full_id[:2], full_id
                suffix = tail[-2:] if len(tail) >= 2 else tail
                type_prefix = dev_type if dev_type in ("06", "07", "08") else (prefix if prefix in ("06", "07", "08") else "")
                short = f"{type_prefix}-{suffix}" if type_prefix else suffix
            except Exception:
                short = full_id[-2:] if len(full_id) >= 2 else full_id
            p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_TEXT)))
            top_y = int(cy - h_px / 2) - 26
            p.drawText(int(cx - 100), top_y, 200, 18, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, short)

    def _draw_plate_logo_single(self, p: QtGui.QPainter, center_mm: Tuple[float, float], w_mm: float, h_mm: float, dev_type: str) -> None:
        """Draw orientation logo text for single-device mode per cheat sheet.
        - For Type 07 (Launch Pad): logo on left side (−X edge).
        - For Type 08 (XL): logo on front side (+Y edge).
        """
        if self.state.display_mode != "single":
            return
        cx, cy = self._to_screen(center_mm[0], center_mm[1])
        scale = self.state.px_per_mm
        w_px = int(w_mm * scale)
        h_px = int(h_mm * scale)
        left_x = int(cx - w_px / 2)
        right_x = int(cx + w_px / 2)
        top_y = int(cy - h_px / 2)
        bottom_y = int(cy + h_px / 2)

        text = "Axioforce"
        p.save()
        # Very dark grey (almost black) font color per request
        p.setPen(QtGui.QPen(QtGui.QColor(30, 30, 30)))
        font = p.font()
        font.setPointSize(max(9, int(10 * scale / max(scale, 1))))
        p.setFont(font)

        if dev_type == "07":
            # Left edge (−X): vertical text along left side, centered
            inset_px = max(6, int(0.04 * w_px) + 5)  # move ~10px further onto plate (+X direction)
            pivot_x = left_x + inset_px
            pivot_y = int((top_y + bottom_y) / 2)
            p.translate(pivot_x, pivot_y)
            p.rotate(-90)
            p.drawText(-int(h_px / 2), -12, h_px, 24, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, text)
        elif dev_type == "08":
            # Front side (+Y): top edge
            p.drawText(int(cx - w_px / 2), top_y + 30, w_px, 18, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, text)
        p.restore()

    def _draw_connection_port_single(self, p: QtGui.QPainter, center_mm: Tuple[float, float], w_mm: float, h_mm: float, dev_type: str) -> None:
        """Draw a dashed rounded rectangle indicating the connection port location for Type 06 and 07.
        Port size: 4.5 in by 2.25 in (114.3 mm by 57.15 mm). Place centered vertically near the
        long side opposite the 07 logo (i.e., near the right edge, slightly inset).
        """
        if self.state.display_mode != "single" or dev_type not in ("07", "06"):
            return
        cx, cy = self._to_screen(center_mm[0], center_mm[1])
        scale = self.state.px_per_mm
        w_px = int(w_mm * scale)
        h_px = int(h_mm * scale)

        # Convert port dimensions (mm → px). Orient long dimension along plate height (Y axis).
        port_h_mm = 4.5 * 25.4
        port_w_mm = 2.25 * 25.4
        port_h_px = int(port_h_mm * scale)
        port_w_px = int(port_w_mm * scale)

        # Position near the right edge, centered vertically
        right_x = int(cx + w_px / 2)
        inset_px = max(12, int(0.03 * w_px))
        rect_left = right_x - inset_px - port_w_px
        rect_top = int(cy - port_h_px / 2)
        rect = QtCore.QRect(rect_left, rect_top, port_w_px, port_h_px)

        # Dashed outline to indicate under-plate object, with rounded corners
        pen = QtGui.QPen(QtGui.QColor(30, 30, 30))
        pen.setStyle(QtCore.Qt.DashLine)
        pen.setWidth(2)
        p.save()
        p.setPen(pen)
        p.setBrush(QtCore.Qt.NoBrush)
        corner_radius = max(6, int(min(port_w_px, port_h_px) * 0.1))
        p.drawRoundedRect(rect, corner_radius, corner_radius)
        p.restore()

    def _draw_placeholder_plate(self, p: QtGui.QPainter) -> None:
        """Draw a greyed out placeholder plate with instruction text when no device is selected."""
        # Use a square size (make it wider by using height for both dimensions)
        w_mm = config.TYPE07_H_MM
        h_mm = config.TYPE07_H_MM
        
        cx, cy = self._to_screen(0.0, 0.0)
        scale = self.state.px_per_mm
        w_px = int(w_mm * scale)
        h_px = int(h_mm * scale)
        rect = QtCore.QRect(int(cx - w_px / 2), int(cy - h_px / 2), w_px, h_px)
        
        # Draw greyed out plate
        grey_fill = QtGui.QColor(80, 80, 80, 150)  # Semi-transparent grey
        grey_outline = QtGui.QColor(100, 100, 100)
        p.setBrush(grey_fill)
        p.setPen(QtGui.QPen(grey_outline, 2, QtCore.Qt.DashLine))
        p.drawRect(rect)
        
        # Draw instruction text in the center
        text_color = QtGui.QColor(180, 180, 180)  # Light grey text
        p.setPen(QtGui.QPen(text_color))
        font = p.font()
        font.setPointSize(14)
        font.setBold(True)
        p.setFont(font)
        
        text = "Choose a device below"
        text_rect = QtCore.QRect(int(cx - w_px / 2), int(cy - 20), w_px, 40)
        p.drawText(text_rect, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, text)
    
    def _draw_mound_placeholder(self, p: QtGui.QPainter, center_mm: Tuple[float, float], w_mm: float, h_mm: float, label: str) -> None:
        """Draw a clickable placeholder plate for mound mode."""
        cx, cy = self._to_screen(center_mm[0], center_mm[1])
        scale = self.state.px_per_mm
        w_px = int(w_mm * scale)
        h_px = int(h_mm * scale)
        rect = QtCore.QRect(int(cx - w_px / 2), int(cy - h_px / 2), w_px, h_px)
        
        # Draw greyed out plate with dashed border
        grey_fill = QtGui.QColor(80, 80, 80, 150)
        grey_outline = QtGui.QColor(120, 120, 120)
        p.setBrush(grey_fill)
        p.setPen(QtGui.QPen(grey_outline, 2, QtCore.Qt.DashLine))
        p.drawRect(rect)
        
        # Draw label text
        text_color = QtGui.QColor(180, 180, 180)
        p.setPen(QtGui.QPen(text_color))
        font = p.font()
        font.setPointSize(10)
        p.setFont(font)
        p.drawText(rect, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, label)

    def _draw_plate_logo_mound(self, p: QtGui.QPainter, center_mm: Tuple[float, float], w_mm: float, h_mm: float) -> None:
        """Draw "Axioforce" along the right side of a plate in mound mode."""
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
        """Get plate dimensions (w_mm, h_mm) based on device type."""
        if not device_id:
            return config.TYPE07_W_MM, config.TYPE07_H_MM  # default
        
        # Find device type from available devices
        for name, axf_id, dev_type in self._available_devices:
            if axf_id == device_id:
                if dev_type == "06":
                    return config.TYPE06_W_MM, config.TYPE06_H_MM
                elif dev_type == "07":
                    return config.TYPE07_W_MM, config.TYPE07_H_MM
                elif dev_type == "08":
                    return config.TYPE08_W_MM, config.TYPE08_H_MM
        
        # Default fallback
        return config.TYPE07_W_MM, config.TYPE07_H_MM

    def _draw_plates(self, p: QtGui.QPainter) -> None:
        if not self.state.flags.show_plates:
            return
        if self.state.display_mode == "single":
            # Check if a device is selected
            if not (self.state.selected_device_id or "").strip():
                # Draw greyed out placeholder plate with instruction text
                self._draw_placeholder_plate(p)
                return
            # Draw a single plate at origin based on selected device type
            dev_type = (self.state.selected_device_type or "").strip()
            if dev_type == "06":
                w_mm = config.TYPE06_W_MM
                h_mm = config.TYPE06_H_MM
            elif dev_type == "07":
                w_mm = config.TYPE07_W_MM
                h_mm = config.TYPE07_H_MM
            else:  # default to 08
                w_mm = config.TYPE08_W_MM
                h_mm = config.TYPE08_H_MM
            self._draw_plate(p, (0.0, 0.0), w_mm, h_mm)
            # Draw orientation logo per cheat sheet
            self._draw_plate_logo_single(p, (0.0, 0.0), w_mm, h_mm, dev_type)
            # Draw connection port marker for 07
            self._draw_connection_port_single(p, (0.0, 0.0), w_mm, h_mm, dev_type)
            return
        
        # Mound layout: Draw plates (placeholder if not assigned, real if assigned)
        # Launch Zone
        launch_device = self.state.mound_devices.get("Launch Zone")
        if launch_device:
            w_mm, h_mm = self._get_plate_dimensions(launch_device)
            self._draw_plate(p, (0.0, 0.0), w_mm, h_mm)
            self._draw_plate_logo_mound(p, (0.0, 0.0), w_mm, h_mm)
        else:
            self._draw_mound_placeholder(p, (0.0, 0.0), config.TYPE07_W_MM, config.TYPE07_H_MM, "Launch Zone\n(Click to select)")
        
        # Lower Landing Zone
        lower_device = self.state.mound_devices.get("Lower Landing Zone")
        if lower_device:
            w_mm, h_mm = self._get_plate_dimensions(lower_device)
            self._draw_plate(p, config.LANDING_LOWER_CENTER_MM, w_mm, h_mm)
            self._draw_plate_logo_mound(p, config.LANDING_LOWER_CENTER_MM, w_mm, h_mm)
        else:
            self._draw_mound_placeholder(p, config.LANDING_LOWER_CENTER_MM, config.TYPE08_W_MM, config.TYPE08_H_MM, "Lower Landing\n(Click to select)")
        
        # Upper Landing Zone
        upper_device = self.state.mound_devices.get("Upper Landing Zone")
        if upper_device:
            w_mm, h_mm = self._get_plate_dimensions(upper_device)
            self._draw_plate(p, config.LANDING_UPPER_CENTER_MM, w_mm, h_mm)
            self._draw_plate_logo_mound(p, config.LANDING_UPPER_CENTER_MM, w_mm, h_mm)
        else:
            self._draw_mound_placeholder(p, config.LANDING_UPPER_CENTER_MM, config.TYPE08_W_MM, config.TYPE08_H_MM, "Upper Landing\n(Click to select)")

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
        # Draw numeric coordinates above the dot
        p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_TEXT)))
        label = f"{x_mm:.1f}, {y_mm:.1f}"
        p.drawText(cx - 60, int(cy - r_px - 24), 120, 18, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, label)
        # Real/raw COP displayed above zone center (not mapped to raw position)
        zone_cx_mm = 0.0
        zone_cy_mm = 0.0 if name == LAUNCH_NAME else config.LANDING_MID_Y_MM
        zx, zy = self._to_screen(zone_cx_mm, zone_cy_mm)
        p.drawText(zx - 70, int(zy - self.state.px_per_mm * (config.TYPE07_H_MM if name == LAUNCH_NAME else config.TYPE08_H_MM) * 0.6),
                   140, 18, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter,
                   f"raw {raw_x_mm:.1f}, {raw_y_mm:.1f}")

    def _draw_cop_single(self, p: QtGui.QPainter, snap: Tuple[float, float, float, int, bool, float, float]) -> None:
        # Ignore show_markers toggle for single mode per user request
        x_mm, y_mm, fz_n, _, is_visible, raw_x_mm, raw_y_mm = snap
        if not is_visible:
            return
        cx, cy = self._to_screen(x_mm, y_mm)
        r_px = max(config.COP_R_MIN_PX, min(config.COP_R_MAX_PX, self.state.cop_scale_k * abs(fz_n)))

        p.setBrush(QtGui.QColor(*config.COLOR_COP_LAUNCH))
        p.setPen(QtGui.QPen(QtCore.Qt.black, 1))
        p.drawEllipse(QtCore.QPoint(cx, cy), int(r_px), int(r_px))
        # Numeric coordinates above the dot
        p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_TEXT)))
        label = f"{x_mm:.1f}, {y_mm:.1f}"
        p.drawText(cx - 60, int(cy - r_px - 24), 120, 18, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, label)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802 (Qt naming)
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.fillRect(0, 0, self.width(), self.height(), QtGui.QColor(*config.COLOR_BG))
        p.setPen(QtGui.QPen(QtGui.QColor(80, 80, 88)))
        p.drawRect(0, 0, max(0, self.width() - 1), max(0, self.height() - 1))
        if not self._fit_done and self.width() > 0 and self.height() > 0:
            self._compute_fit()
        self._draw_grid(p)
        self._draw_plates(p)
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
        p.end()

    def _draw_plate_names(self, p: QtGui.QPainter) -> None:
        return



class ForcePlotWidget(QtWidgets.QWidget):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(160)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)
        hdr = QtWidgets.QHBoxLayout()
        self.title = QtWidgets.QLabel("Force Plot (ΣX, ΣY, ΣZ)")
        self.title.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        hdr.addWidget(self.title)
        hdr.addStretch(1)
        root.addLayout(hdr)
        self._samples: list[tuple[int, float, float, float]] = []  # (t_ms, fx, fy, fz)
        self._max_points = 600  # ~10s at 60 Hz
        self._auto_scale = True
        self._y_min = -10.0
        self._y_max = 10.0

    def clear(self) -> None:
        self._samples.clear()
        self.update()

    def add_point(self, t_ms: int, fx: float, fy: float, fz: float) -> None:
        self._samples.append((t_ms, fx, fy, fz))
        if len(self._samples) > self._max_points:
            self._samples = self._samples[-self._max_points:]
        if self._auto_scale:
            try:
                vals = [abs(fx), abs(fy), abs(fz)]
                current_max = max(vals + [1.0])
                target = max(current_max * 1.2, 5.0)
                self._y_max = max(self._y_max * 0.9 + target * 0.1, target)
                self._y_min = -self._y_max
            except Exception:
                pass
        self.update()

    def paintEvent(self, _e: QtGui.QPaintEvent) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        # Background
        p.fillRect(0, 0, w, h, QtGui.QColor(*config.COLOR_BG))
        # Plot area margins
        m_left, m_right, m_top, m_bottom = 36, 12, 6, 18
        x0, y0 = m_left, m_top
        pw, ph = max(1, w - m_left - m_right), max(1, h - m_top - m_bottom)
        # Axes
        axis_pen = QtGui.QPen(QtGui.QColor(180, 180, 180))
        axis_pen.setWidth(1)
        p.setPen(axis_pen)
        p.drawRect(x0, y0, pw, ph)
        # Zero line
        if self._y_min < 0 < self._y_max:
            zy = int(y0 + ph * (1 - (0 - self._y_min) / (self._y_max - self._y_min)))
            p.drawLine(x0, zy, x0 + pw, zy)
        # No data
        if not self._samples:
            p.end()
            return
        # X scale by index (uniform time assumptions)
        n = len(self._samples)
        def to_xy(i: int, v: float) -> tuple[int, int]:
            x = x0 + int(pw * (i / max(1, self._max_points - 1)))
            y = y0 + int(ph * (1 - (v - self._y_min) / max(1e-6, (self._y_max - self._y_min))))
            return x, y
        # Series pens
        pen_x = QtGui.QPen(QtGui.QColor(220, 80, 80))
        pen_y = QtGui.QPen(QtGui.QColor(80, 180, 220))
        pen_z = QtGui.QPen(QtGui.QColor(120, 220, 120))
        pen_x.setWidth(2); pen_y.setWidth(2); pen_z.setWidth(2)
        # Draw polylines
        for idx, (pen, comp) in enumerate(((pen_x, 1), (pen_y, 2), (pen_z, 3))):
            p.setPen(pen)
            path = QtGui.QPainterPath()
            i0 = max(0, n - self._max_points)
            for i in range(i0, n):
                t_ms, fx, fy, fz = self._samples[i]
                v = fx if comp == 1 else fy if comp == 2 else fz
                x, y = to_xy(i - i0, v)
                if i == i0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            p.drawPath(path)
        # Legend
        p.setPen(QtGui.QColor(200, 200, 200))
        p.drawText(x0 + 6, y0 + 14, "ΣFx")
        p.drawText(x0 + 46, y0 + 14, "ΣFy")
        p.drawText(x0 + 86, y0 + 14, "ΣFz")
        p.end()

    def set_snapshots(self, snaps: Dict[str, Tuple[float, float, float, int, bool, float, float]]) -> None:
        self._snapshots = snaps
        # Update single snapshot if the selected device id matches a known key
        sid = (self.state.selected_device_id or "").strip()
        if sid and sid in self._snapshots:
            self._single_snapshot = self._snapshots.get(sid)
        self.update()

    def set_single_snapshot(self, snap: Optional[Tuple[float, float, float, int, bool, float, float]]) -> None:
        self._single_snapshot = snap
        if self.state.display_mode == "single":
            self.update()
    
    def set_available_devices(self, devices: List[Tuple[str, str, str]]) -> None:
        """Store available devices for device picker."""
        self._available_devices = devices
    
    def update_active_devices(self, active_device_ids: set) -> None:
        """Update the set of active device IDs."""
        self._active_device_ids = active_device_ids

    def _canvas_resizeEvent_UNUSED(self, event: QtGui.QResizeEvent) -> None:  # noqa: N802 (Qt naming)
        # Invalidate fit so we recompute with the new size
        self._fit_done = False
        super().resizeEvent(event)
    
    def _canvas_mousePressEvent_UNUSED(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802 (Qt naming)
        """Handle clicks on plates and labels in mound mode."""
        if self.state.display_mode != "mound" or event.button() != QtCore.Qt.LeftButton:
            return super().mousePressEvent(event)
        
        pos = event.pos()
        
        # Check if clicking on a plate or label
        clicked_position = self._get_clicked_position(pos)
        if clicked_position:
            self._show_device_picker(clicked_position)
        
        super().mousePressEvent(event)

    def _compute_world_bounds(self) -> None:
        # Compute bounds based on display mode
        if self.state.display_mode == "single":
            # Single device: center at origin, bounds based on device type
            is_07 = (self.state.selected_device_type or "").strip() == "07"
            half_w = (config.TYPE07_W_MM if is_07 else config.TYPE08_W_MM) / 2.0
            half_h = (config.TYPE07_H_MM if is_07 else config.TYPE08_H_MM) / 2.0
            # Add margin around plate
            margin_mm = 200.0
            self.WORLD_X_MIN, self.WORLD_X_MAX = -half_h - margin_mm, half_h + margin_mm
            self.WORLD_Y_MIN, self.WORLD_Y_MAX = -half_w - margin_mm, half_w + margin_mm
            return

        # Mound: include all three plates fully with padding
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
        # Add margin around the mound layout
        margin_mm = 150.0
        self.WORLD_X_MIN, self.WORLD_X_MAX = x_min - margin_mm, x_max + margin_mm
        self.WORLD_Y_MIN, self.WORLD_Y_MAX = y_min - margin_mm, y_max + margin_mm

    def _compute_fit(self) -> None:
        w, h = self.width(), self.height()
        self._compute_world_bounds()
        # Rotated framing: width uses Y-range, height uses X-range
        world_w = self.WORLD_Y_MAX - self.WORLD_Y_MIN
        world_h = self.WORLD_X_MAX - self.WORLD_X_MIN
        s = min((w - 2 * self.MARGIN_PX) / world_w, (h - 2 * self.MARGIN_PX) / world_h)
        self.state.px_per_mm = max(0.01, s)
        # Center world bbox in the window
        self._y_mid = (self.WORLD_Y_MIN + self.WORLD_Y_MAX) / 2.0
        self._x_mid = (self.WORLD_X_MIN + self.WORLD_X_MAX) / 2.0
        self._fit_done = True

    # World to screen transform:
    # - Single mode: normal axes (+X → right, +Y → up)
    # - Mound mode: rotated framing (+Y → right, +X → up)
    def _to_screen(self, x_mm: float, y_mm: float) -> Tuple[int, int]:
        w, h = self.width(), self.height()
        s = self.state.px_per_mm
        assert s > 0
        cx, cy = w * 0.5, h * 0.5
        if self.state.display_mode == "single":
            # Normal mapping: X→right, Y→up
            sx = int(cx + (x_mm - self._x_mid) * s)
            sy = int(cy - (y_mm - self._y_mid) * s)
        else:
            # Rotated mapping for mound layout: Y→right, X→up
            sx = int(cx + (y_mm - self._y_mid) * s)
            sy = int(cy - (x_mm - self._x_mid) * s)
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

        # Always show small axis indicator

        # Axis indicator in bottom-left corner (not full axes)
        base_x, base_y = 12, h - 12
        length = 60
        
        # For single device mode, use normal X and Y axes
        if self.state.display_mode == "single":
            # X
            p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_AXIS_X), config.AXIS_THICKNESS_PX))
            p.drawLine(base_x, base_y, base_x + length, base_y)
            # Y
            p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_AXIS_Y), config.AXIS_THICKNESS_PX))
            p.drawLine(base_x, base_y, base_x, base_y - length)
            # Labels
            p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_TEXT)))
            p.drawText(base_x + length + 6, base_y + 4, "X")
            p.drawText(base_x - 10, base_y - length - 6, "Y")
        else:
            # Mound mode: Y+, X+ (rotated orientation)
            # Y+
            p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_AXIS_Y), config.AXIS_THICKNESS_PX))
            p.drawLine(base_x, base_y, base_x + length, base_y)
            # X+
            p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_AXIS_X), config.AXIS_THICKNESS_PX))
            p.drawLine(base_x, base_y, base_x, base_y - length)
            # Labels
            p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_TEXT)))
            p.drawText(base_x + length + 6, base_y + 4, "Y+")
            p.drawText(base_x - 10, base_y - length - 6, "X+")

    def _draw_plate(self, p: QtGui.QPainter, center_mm: Tuple[float, float], w_mm: float, h_mm: float) -> None:
        cx, cy = self._to_screen(center_mm[0], center_mm[1])
        scale = self.state.px_per_mm
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
        # In single-device mode, annotate plate with shortened device id at the top
        if self.state.display_mode == "single" and (self.state.selected_device_id or "").strip():
            full_id = (self.state.selected_device_id or "").strip()
            dev_type = (self.state.selected_device_type or "").strip()
            # Build short id: type (07/08) followed by '-' and last two chars of unique id
            try:
                if "-" in full_id:
                    prefix, tail = full_id.split("-", 1)
                else:
                    prefix, tail = full_id[:2], full_id
                suffix = tail[-2:] if len(tail) >= 2 else tail
                type_prefix = dev_type if dev_type in ("06", "07", "08") else (prefix if prefix in ("06", "07", "08") else "")
                short = f"{type_prefix}-{suffix}" if type_prefix else suffix
            except Exception:
                short = full_id[-2:] if len(full_id) >= 2 else full_id
            p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_TEXT)))
            top_y = int(cy - h_px / 2) - 26
            p.drawText(int(cx - 100), top_y, 200, 18, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, short)

    def _draw_plate_logo_single(self, p: QtGui.QPainter, center_mm: Tuple[float, float], w_mm: float, h_mm: float, dev_type: str) -> None:
        """Draw orientation logo text for single-device mode per cheat sheet.
        - For Type 07 (Launch Pad): logo on left side (−X edge).
        - For Type 08 (XL): logo on front side (+Y edge).
        """
        if self.state.display_mode != "single":
            return
        cx, cy = self._to_screen(center_mm[0], center_mm[1])
        scale = self.state.px_per_mm
        w_px = int(w_mm * scale)
        h_px = int(h_mm * scale)
        left_x = int(cx - w_px / 2)
        right_x = int(cx + w_px / 2)
        top_y = int(cy - h_px / 2)
        bottom_y = int(cy + h_px / 2)

        text = "Axioforce"
        p.save()
        # Very dark grey (almost black) font color per request
        p.setPen(QtGui.QPen(QtGui.QColor(30, 30, 30)))
        font = p.font()
        font.setPointSize(max(9, int(10 * scale / max(scale, 1))))
        p.setFont(font)

        if dev_type == "07":
            # Left edge (−X): vertical text along left side, centered
            inset_px = max(6, int(0.04 * w_px) + 5)  # move ~10px further onto plate (+X direction)
            pivot_x = left_x + inset_px
            pivot_y = int((top_y + bottom_y) / 2)
            p.translate(pivot_x, pivot_y)
            p.rotate(-90)
            p.drawText(-int(h_px / 2), -12, h_px, 24, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, text)
        elif dev_type == "08":
            # Front side (+Y): top edge
            p.drawText(int(cx - w_px / 2), top_y + 30, w_px, 18, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, text)
        p.restore()

    def _draw_connection_port_single(self, p: QtGui.QPainter, center_mm: Tuple[float, float], w_mm: float, h_mm: float, dev_type: str) -> None:
        """Draw a dashed rounded rectangle indicating the connection port location for Type 06 and 07.
        Port size: 4.5 in by 2.25 in (114.3 mm by 57.15 mm). Place centered vertically near the
        long side opposite the 07 logo (i.e., near the right edge, slightly inset).
        """
        if self.state.display_mode != "single" or dev_type not in ("07", "06"):
            return
        cx, cy = self._to_screen(center_mm[0], center_mm[1])
        scale = self.state.px_per_mm
        w_px = int(w_mm * scale)
        h_px = int(h_mm * scale)

        # Convert port dimensions (mm → px). Orient long dimension along plate height (Y axis).
        port_h_mm = 4.5 * 25.4
        port_w_mm = 2.25 * 25.4
        port_h_px = int(port_h_mm * scale)
        port_w_px = int(port_w_mm * scale)

        # Position near the right edge, centered vertically
        right_x = int(cx + w_px / 2)
        inset_px = max(12, int(0.03 * w_px))
        rect_left = right_x - inset_px - port_w_px
        rect_top = int(cy - port_h_px / 2)
        rect = QtCore.QRect(rect_left, rect_top, port_w_px, port_h_px)

        # Dashed outline to indicate under-plate object, with rounded corners
        pen = QtGui.QPen(QtGui.QColor(30, 30, 30))
        pen.setStyle(QtCore.Qt.DashLine)
        pen.setWidth(2)
        p.save()
        p.setPen(pen)
        p.setBrush(QtCore.Qt.NoBrush)
        corner_radius = max(6, int(min(port_w_px, port_h_px) * 0.1))
        p.drawRoundedRect(rect, corner_radius, corner_radius)
        p.restore()

    def _draw_placeholder_plate(self, p: QtGui.QPainter) -> None:
        """Draw a greyed out placeholder plate with instruction text when no device is selected."""
        # Use a square size (make it wider by using height for both dimensions)
        w_mm = config.TYPE07_H_MM
        h_mm = config.TYPE07_H_MM
        
        cx, cy = self._to_screen(0.0, 0.0)
        scale = self.state.px_per_mm
        w_px = int(w_mm * scale)
        h_px = int(h_mm * scale)
        rect = QtCore.QRect(int(cx - w_px / 2), int(cy - h_px / 2), w_px, h_px)
        
        # Draw greyed out plate
        grey_fill = QtGui.QColor(80, 80, 80, 150)  # Semi-transparent grey
        grey_outline = QtGui.QColor(100, 100, 100)
        p.setBrush(grey_fill)
        p.setPen(QtGui.QPen(grey_outline, 2, QtCore.Qt.DashLine))
        p.drawRect(rect)
        
        # Draw instruction text in the center
        text_color = QtGui.QColor(180, 180, 180)  # Light grey text
        p.setPen(QtGui.QPen(text_color))
        font = p.font()
        font.setPointSize(14)
        font.setBold(True)
        p.setFont(font)
        
        text = "Choose a device below"
        text_rect = QtCore.QRect(int(cx - w_px / 2), int(cy - 20), w_px, 40)
        p.drawText(text_rect, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, text)
    
    def _draw_mound_placeholder(self, p: QtGui.QPainter, center_mm: Tuple[float, float], w_mm: float, h_mm: float, label: str) -> None:
        """Draw a clickable placeholder plate for mound mode."""
        cx, cy = self._to_screen(center_mm[0], center_mm[1])
        scale = self.state.px_per_mm
        w_px = int(w_mm * scale)
        h_px = int(h_mm * scale)
        rect = QtCore.QRect(int(cx - w_px / 2), int(cy - h_px / 2), w_px, h_px)
        
        # Draw greyed out plate with dashed border
        grey_fill = QtGui.QColor(80, 80, 80, 150)
        grey_outline = QtGui.QColor(120, 120, 120)
        p.setBrush(grey_fill)
        p.setPen(QtGui.QPen(grey_outline, 2, QtCore.Qt.DashLine))
        p.drawRect(rect)
        
        # Draw label text
        text_color = QtGui.QColor(180, 180, 180)
        p.setPen(QtGui.QPen(text_color))
        font = p.font()
        font.setPointSize(10)
        p.setFont(font)
        p.drawText(rect, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, label)

    def _draw_plate_logo_mound(self, p: QtGui.QPainter, center_mm: Tuple[float, float], w_mm: float, h_mm: float) -> None:
        """Draw "Axioforce" along the right side of a plate in mound mode."""
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
        """Get plate dimensions (w_mm, h_mm) based on device type."""
        if not device_id:
            return config.TYPE07_W_MM, config.TYPE07_H_MM  # default
        
        # Find device type from available devices
        for name, axf_id, dev_type in self._available_devices:
            if axf_id == device_id:
                if dev_type == "06":
                    return config.TYPE06_W_MM, config.TYPE06_H_MM
                elif dev_type == "07":
                    return config.TYPE07_W_MM, config.TYPE07_H_MM
                elif dev_type == "08":
                    return config.TYPE08_W_MM, config.TYPE08_H_MM
        
        # Default fallback
        return config.TYPE07_W_MM, config.TYPE07_H_MM

    def _draw_plates(self, p: QtGui.QPainter) -> None:
        if not self.state.flags.show_plates:
            return
        if self.state.display_mode == "single":
            # Check if a device is selected
            if not (self.state.selected_device_id or "").strip():
                # Draw greyed out placeholder plate with instruction text
                self._draw_placeholder_plate(p)
                return
            # Draw a single plate at origin based on selected device type
            dev_type = (self.state.selected_device_type or "").strip()
            if dev_type == "06":
                w_mm = config.TYPE06_W_MM
                h_mm = config.TYPE06_H_MM
            elif dev_type == "07":
                w_mm = config.TYPE07_W_MM
                h_mm = config.TYPE07_H_MM
            else:  # default to 08
                w_mm = config.TYPE08_W_MM
                h_mm = config.TYPE08_H_MM
            self._draw_plate(p, (0.0, 0.0), w_mm, h_mm)
            # Draw orientation logo per cheat sheet
            self._draw_plate_logo_single(p, (0.0, 0.0), w_mm, h_mm, dev_type)
            # Draw connection port marker for 07
            self._draw_connection_port_single(p, (0.0, 0.0), w_mm, h_mm, dev_type)
            return
        
        # Mound layout: Draw plates (placeholder if not assigned, real if assigned)
        # Launch Zone
        launch_device = self.state.mound_devices.get("Launch Zone")
        if launch_device:
            w_mm, h_mm = self._get_plate_dimensions(launch_device)
            self._draw_plate(p, (0.0, 0.0), w_mm, h_mm)
            self._draw_plate_logo_mound(p, (0.0, 0.0), w_mm, h_mm)
        else:
            self._draw_mound_placeholder(p, (0.0, 0.0), config.TYPE07_W_MM, config.TYPE07_H_MM, "Launch Zone\n(Click to select)")
        
        # Lower Landing Zone
        lower_device = self.state.mound_devices.get("Lower Landing Zone")
        if lower_device:
            w_mm, h_mm = self._get_plate_dimensions(lower_device)
            self._draw_plate(p, config.LANDING_LOWER_CENTER_MM, w_mm, h_mm)
            self._draw_plate_logo_mound(p, config.LANDING_LOWER_CENTER_MM, w_mm, h_mm)
        else:
            self._draw_mound_placeholder(p, config.LANDING_LOWER_CENTER_MM, config.TYPE08_W_MM, config.TYPE08_H_MM, "Lower Landing\n(Click to select)")
        
        # Upper Landing Zone
        upper_device = self.state.mound_devices.get("Upper Landing Zone")
        if upper_device:
            w_mm, h_mm = self._get_plate_dimensions(upper_device)
            self._draw_plate(p, config.LANDING_UPPER_CENTER_MM, w_mm, h_mm)
            self._draw_plate_logo_mound(p, config.LANDING_UPPER_CENTER_MM, w_mm, h_mm)
        else:
            self._draw_mound_placeholder(p, config.LANDING_UPPER_CENTER_MM, config.TYPE08_W_MM, config.TYPE08_H_MM, "Upper Landing\n(Click to select)")

        # Draw shortened ids above each plate (mound mode)
        self._draw_short_ids_mound(p)

        # Validation (non-fatal logging while backend COP is being tuned)
        scale = self.state.px_per_mm
        # No multi-plate order check while focusing on Launch only

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
        # Draw numeric coordinates above the dot
        p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_TEXT)))
        label = f"{x_mm:.1f}, {y_mm:.1f}"
        p.drawText(cx - 60, int(cy - r_px - 24), 120, 18, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, label)
        # Real/raw COP displayed above zone center (not mapped to raw position)
        zone_cx_mm = 0.0
        zone_cy_mm = 0.0 if name == LAUNCH_NAME else config.LANDING_MID_Y_MM
        zx, zy = self._to_screen(zone_cx_mm, zone_cy_mm)
        p.drawText(zx - 70, int(zy - self.state.px_per_mm *  (config.TYPE07_H_MM if name == LAUNCH_NAME else config.TYPE08_H_MM) * 0.6),
                   140, 18, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter,
                   f"raw {raw_x_mm:.1f}, {raw_y_mm:.1f}")

    def _draw_cop_single(self, p: QtGui.QPainter, snap: Tuple[float, float, float, int, bool, float, float]) -> None:
        # Ignore show_markers toggle for single mode per user request
        x_mm, y_mm, fz_n, _, is_visible, raw_x_mm, raw_y_mm = snap
        if not is_visible:
            return
        cx, cy = self._to_screen(x_mm, y_mm)
        r_px = max(config.COP_R_MIN_PX, min(config.COP_R_MAX_PX, self.state.cop_scale_k * abs(fz_n)))

        p.setBrush(QtGui.QColor(*config.COLOR_COP_LAUNCH))
        p.setPen(QtGui.QPen(QtCore.Qt.black, 1))
        p.drawEllipse(QtCore.QPoint(cx, cy), int(r_px), int(r_px))
        # Numeric coordinates above the dot
        p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_TEXT)))
        label = f"{x_mm:.1f}, {y_mm:.1f}"
        p.drawText(cx - 60, int(cy - r_px - 24), 120, 18, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, label)

    def _canvas_paintEvent_UNUSED(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802 (Qt naming)
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        # Explicitly paint background to ensure visibility regardless of palette/style
        p.fillRect(0, 0, self.width(), self.height(), QtGui.QColor(*config.COLOR_BG))
        # Optional subtle border to ensure the canvas area is visible
        p.setPen(QtGui.QPen(QtGui.QColor(80, 80, 88)))
        p.drawRect(0, 0, max(0, self.width() - 1), max(0, self.height() - 1))
        if not self._fit_done and self.width() > 0 and self.height() > 0:
            self._compute_fit()
        self._draw_grid(p)
        self._draw_plates(p)
        if self.state.display_mode == "single":
            if self._single_snapshot is not None:
                # Render single device COP without mound orientation adjustments
                self._draw_cop_single(p, self._single_snapshot)
        else:
            # Only draw COP in mound mode if all devices are configured
            all_configured = all(self.state.mound_devices.get(pos) for pos in ["Launch Zone", "Upper Landing Zone", "Lower Landing Zone"])
            if all_configured:
                for name, snap in self._snapshots.items():
                    if name in (LAUNCH_NAME, LANDING_NAME):
                        self._draw_cop(p, name, snap)
        # Draw labels last (disabled per request; only shortened IDs are shown)
        self._draw_plate_names(p)
        p.end()

    def _draw_plate_names(self, p: QtGui.QPainter) -> None:
        # Intentionally no-op: suppress device/plate name labels over the grid
        return

    def _draw_plates(self, p: QtGui.QPainter) -> None:
        # Temporary minimal implementation to avoid crashes; full logic lives below
        if not getattr(self.state.flags, "show_plates", True):
            return
        # Draw a simple placeholder plate at origin to restore visual feedback
        scale = self.state.px_per_mm
        w_mm = config.TYPE07_W_MM
        h_mm = config.TYPE07_H_MM
        cx, cy = self._to_screen(0.0, 0.0)
        w_px = int(w_mm * scale)
        h_px = int(h_mm * scale)
        rect = QtCore.QRect(int(cx - w_px / 2), int(cy - h_px / 2), w_px, h_px)
        p.setBrush(QtGui.QColor(*config.COLOR_PLATE))
        p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_PLATE_OUTLINE), 2))
        p.drawRect(rect)

    def _draw_cop(self, p: QtGui.QPainter, name: str, snap: Tuple[float, float, float, int, bool, float, float]) -> None:
        # Minimal no-op to satisfy calls; full logic is defined later in the file
        return

    def _draw_cop_single(self, p: QtGui.QPainter, snap: Tuple[float, float, float, int, bool, float, float]) -> None:
        # Minimal no-op to satisfy calls; full logic is defined later in the file
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
            type_prefix = dev_type_hint if dev_type_hint in ("06", "07", "08") else (prefix if prefix in ("06", "07", "08") else "")
            return f"{type_prefix}-{suffix}" if type_prefix else suffix
        except Exception:
            return full[-2:] if len(full) >= 2 else full

    def _draw_short_ids_mound(self, p: QtGui.QPainter) -> None:
        # Compute y positions slightly higher to avoid overlapping plate border
        p.setPen(QtGui.QPen(QtGui.QColor(*config.COLOR_TEXT)))
        scale = self.state.px_per_mm
        # Launch
        cx, cy = self._to_screen(0.0, 0.0)
        h_px_launch = int(config.TYPE07_H_MM * scale)
        sid_launch = self._short_id_from_full(self.state.mound_devices.get("Launch Zone", ""), "07")
        p.drawText(int(cx - 100), int(cy - h_px_launch / 2) - 26, 200, 18, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, sid_launch)
        # Landing lower
        llx, lly = self._to_screen(config.LANDING_LOWER_CENTER_MM[0], config.LANDING_LOWER_CENTER_MM[1])
        h_px_l = int(config.TYPE08_H_MM * scale)
        sid_lower = self._short_id_from_full(self.state.mound_devices.get("Lower Landing Zone", ""), "08")
        p.drawText(int(llx - 100), int(lly - h_px_l / 2) - 26, 200, 18, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, sid_lower)
        # Landing upper
        lux, luy = self._to_screen(config.LANDING_UPPER_CENTER_MM[0], config.LANDING_UPPER_CENTER_MM[1])
        sid_upper = self._short_id_from_full(self.state.mound_devices.get("Upper Landing Zone", ""), "08")
        p.drawText(int(lux - 100), int(luy - h_px_l / 2) - 26, 200, 18, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, sid_upper)
    
    def _get_clicked_position(self, pos: QtCore.QPoint) -> Optional[str]:
        """Determine which mound position was clicked based on mouse position."""
        if not self._fit_done:
            return None
            
        scale = self.state.px_per_mm
        
        # Check Launch Zone
        cx, cy = self._to_screen(0.0, 0.0)
        w_px = int(config.TYPE07_W_MM * scale)
        h_px = int(config.TYPE07_H_MM * scale)
        rect = QtCore.QRect(int(cx - w_px / 2), int(cy - h_px / 2), w_px, h_px)
        # Include label area
        label_rect = QtCore.QRect(int(cx - 100), int(cy - h_px / 2) - 26, 200, 18)
        if rect.contains(pos) or label_rect.contains(pos):
            return "Launch Zone"
        
        # Check Lower Landing Zone
        llx, lly = self._to_screen(config.LANDING_LOWER_CENTER_MM[0], config.LANDING_LOWER_CENTER_MM[1])
        w_px_l = int(config.TYPE08_W_MM * scale)
        h_px_l = int(config.TYPE08_H_MM * scale)
        rect = QtCore.QRect(int(llx - w_px_l / 2), int(lly - h_px_l / 2), w_px_l, h_px_l)
        label_rect = QtCore.QRect(int(llx - 100), int(lly - h_px_l / 2) - 26, 200, 18)
        if rect.contains(pos) or label_rect.contains(pos):
            return "Lower Landing Zone"
        
        # Check Upper Landing Zone
        lux, luy = self._to_screen(config.LANDING_UPPER_CENTER_MM[0], config.LANDING_UPPER_CENTER_MM[1])
        rect = QtCore.QRect(int(lux - w_px_l / 2), int(luy - h_px_l / 2), w_px_l, h_px_l)
        label_rect = QtCore.QRect(int(lux - 100), int(luy - h_px_l / 2) - 26, 200, 18)
        if rect.contains(pos) or label_rect.contains(pos):
            return "Upper Landing Zone"
        
        return None
    
    def _show_device_picker(self, position_id: str) -> None:
        """Show device picker dialog for the given mound position."""
        # Determine required device type by mound position
        if position_id == "Launch Zone":
            required_type = "07"
        else:
            # Both landing zones require Type 08
            required_type = "08"

        # Use all available devices of the required type (not just active)
        devices_for_picker: List[Tuple[str, str, str]] = []
        for name, axf_id, dev_type in self._available_devices:
            if dev_type == required_type:
                devices_for_picker.append((name, axf_id, dev_type))

        dialog = DevicePickerDialog(position_id, required_type, devices_for_picker, self)
        result = dialog.exec()
        
        if result == QtWidgets.QDialog.Accepted and dialog.selected_device:
            name, axf_id, dev_type = dialog.selected_device
            # Update state
            self.state.mound_devices[position_id] = axf_id
            # Emit signal
            self.mound_device_selected.emit(position_id, axf_id)
            # Trigger repaint
            self.update()


class ControlPanel(QtWidgets.QWidget):
    connect_requested = QtCore.Signal(str, int)
    disconnect_requested = QtCore.Signal()
    start_capture_requested = QtCore.Signal(dict)
    stop_capture_requested = QtCore.Signal(dict)
    tare_requested = QtCore.Signal(str)
    scale_changed = QtCore.Signal(float)
    flags_changed = QtCore.Signal()
    config_changed = QtCore.Signal()
    refresh_devices_requested = QtCore.Signal()

    def __init__(self, state: ViewState, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.state = state

        # Helpers to normalize control sizes
        def _fixh(w: QtWidgets.QWidget, h: int = 22) -> None:
            w.setFixedHeight(h)

        def _fix_btn(b: QtWidgets.QPushButton, wmin: int = 110, h: int = 26) -> None:
            b.setFixedHeight(h)
            b.setMinimumWidth(wmin)
            b.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(6, 6, 6, 6)

        # No Tare here; Tare is overlaid on the canvas in the main window

        # Categorized tabs
        tabs = QtWidgets.QTabWidget()
        self.tabs = tabs

        # Connection tab
        connection_tab = QtWidgets.QWidget()
        conn_layout = QtWidgets.QGridLayout(connection_tab)
        conn_layout.setVerticalSpacing(12)
        conn_row = 0
        
        # Host and port in first row
        conn_host_port_row = QtWidgets.QWidget()
        conn_host_port_layout = QtWidgets.QHBoxLayout(conn_host_port_row)
        conn_host_port_layout.setContentsMargins(0, 0, 0, 0)
        conn_host_port_layout.setSpacing(10)
        conn_host_port_layout.addWidget(QtWidgets.QLabel("Host:"))
        self.host_edit = QtWidgets.QLineEdit(config.SOCKET_HOST)
        _fixh(self.host_edit)
        self.host_edit.setMaximumWidth(220)
        conn_host_port_layout.addWidget(self.host_edit)
        conn_host_port_layout.addWidget(QtWidgets.QLabel("Port:"))
        self.port_spin = QtWidgets.QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(config.SOCKET_PORT)
        _fixh(self.port_spin)
        self.port_spin.setMaximumWidth(80)
        conn_host_port_layout.addWidget(self.port_spin)
        conn_host_port_layout.addStretch(1)
        conn_layout.addWidget(conn_host_port_row, conn_row, 0, 1, 4)
        conn_row += 1
        
        # Connect and disconnect buttons in second row
        conn_buttons_row = QtWidgets.QWidget()
        conn_buttons_layout = QtWidgets.QHBoxLayout(conn_buttons_row)
        conn_buttons_layout.setContentsMargins(0, 0, 0, 0)
        conn_buttons_layout.setSpacing(10)
        self.btn_connect = QtWidgets.QPushButton("Connect")
        self.btn_disconnect = QtWidgets.QPushButton("Disconnect")
        _fix_btn(self.btn_connect, 120)
        _fix_btn(self.btn_disconnect, 120)
        conn_buttons_layout.addWidget(self.btn_connect)
        conn_buttons_layout.addWidget(self.btn_disconnect)
        conn_buttons_layout.addStretch(1)
        conn_layout.addWidget(conn_buttons_row, conn_row, 0, 1, 4)
        conn_row += 1
        
        # Add stretch to push content to top
        conn_layout.setRowStretch(conn_row, 1)

        # tabs: Connection added later after all tabs are constructed

        # Demo tab (metadata + capture controls)
        demo_tab = QtWidgets.QWidget()
        demo_layout = QtWidgets.QGridLayout(demo_tab)
        demo_layout.setVerticalSpacing(12)
        demo_row = 0
        
        # Group ID and Athlete ID in first row
        ids_row = QtWidgets.QWidget()
        ids_layout = QtWidgets.QHBoxLayout(ids_row)
        ids_layout.setContentsMargins(0, 0, 0, 0)
        ids_layout.setSpacing(10)
        ids_layout.addWidget(QtWidgets.QLabel("Group ID:"))
        self.group_edit = QtWidgets.QLineEdit()
        _fixh(self.group_edit)
        self.group_edit.setMaximumWidth(260)
        ids_layout.addWidget(self.group_edit)
        ids_layout.addWidget(QtWidgets.QLabel("Athlete ID:"))
        self.athlete_edit = QtWidgets.QLineEdit()
        _fixh(self.athlete_edit)
        self.athlete_edit.setMaximumWidth(260)
        ids_layout.addWidget(self.athlete_edit)
        ids_layout.addStretch(1)
        demo_layout.addWidget(ids_row, demo_row, 0, 1, 3)
        demo_row += 1

        # Capture Type in second row
        capture_type_row = QtWidgets.QWidget()
        capture_type_layout = QtWidgets.QHBoxLayout(capture_type_row)
        capture_type_layout.setContentsMargins(0, 0, 0, 0)
        capture_type_layout.setSpacing(10)
        capture_type_layout.addWidget(QtWidgets.QLabel("Capture Type:"))
        self.capture_type = QtWidgets.QComboBox()
        self.capture_type.addItems(["pitch", "other"])  # extend as needed
        _fixh(self.capture_type)
        self.capture_type.setMaximumWidth(140)
        capture_type_layout.addWidget(self.capture_type)
        capture_type_layout.addStretch(1)
        demo_layout.addWidget(capture_type_row, demo_row, 0, 1, 3)
        demo_row += 1

        # Start/Stop buttons in third row
        btn_row_widget = QtWidgets.QWidget()
        btn_row = QtWidgets.QHBoxLayout(btn_row_widget)
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(10)
        self.btn_start = QtWidgets.QPushButton("Start Capture")
        self.btn_stop = QtWidgets.QPushButton("Stop Capture")
        _fix_btn(self.btn_start, 130)
        _fix_btn(self.btn_stop, 130)
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_stop)
        btn_row.addStretch(1)
        demo_layout.addWidget(btn_row_widget, demo_row, 0, 1, 3)
        demo_row += 1
        
        # Add stretch to push content to top
        demo_layout.setRowStretch(demo_row, 1)

        # tabs: Demo added later after all tabs are constructed

        # Interface tab (visual toggles + COP scale)
        interface_tab = QtWidgets.QWidget()
        iface_layout = QtWidgets.QGridLayout(interface_tab)
        iface_layout.setVerticalSpacing(12)
        iface_row = 0
        
        # COP Scale slider in first row
        cop_scale_row = QtWidgets.QWidget()
        cop_scale_layout = QtWidgets.QHBoxLayout(cop_scale_row)
        cop_scale_layout.setContentsMargins(0, 0, 0, 0)
        cop_scale_layout.setSpacing(10)
        cop_scale_layout.addWidget(QtWidgets.QLabel("COP Scale (px/N):"))
        self.scale_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.scale_slider.setRange(1, 50)  # 0.01 to 0.50
        self.scale_slider.setValue(int(self.state.cop_scale_k * 100))
        self.scale_slider.setFixedHeight(8)
        self.scale_slider.setStyleSheet(
            "QSlider::groove:horizontal{height:6px;background:#444;border-radius:3px;}"
            "QSlider::handle:horizontal{background:#AAA;width:10px;height:10px;margin:-4px 0;border-radius:5px;}"
        )
        cop_scale_layout.addWidget(self.scale_slider)
        self.scale_label = QtWidgets.QLabel(f"{self.state.cop_scale_k:.2f}")
        _fixh(self.scale_label, 20)
        cop_scale_layout.addWidget(self.scale_label)
        cop_scale_layout.addStretch(1)
        iface_layout.addWidget(cop_scale_row, iface_row, 0, 1, 3)
        iface_row += 1

        # Checkboxes in second row
        checkboxes_row = QtWidgets.QWidget()
        checkboxes_layout = QtWidgets.QHBoxLayout(checkboxes_row)
        checkboxes_layout.setContentsMargins(0, 0, 0, 0)
        checkboxes_layout.setSpacing(10)
        self.chk_plates = QtWidgets.QCheckBox("Show Plates")
        self.chk_plates.setChecked(self.state.flags.show_plates)
        self.chk_labels = QtWidgets.QCheckBox("Show Labels")
        self.chk_labels.setChecked(self.state.flags.show_labels)
        checkboxes_layout.addWidget(self.chk_plates)
        checkboxes_layout.addWidget(self.chk_labels)
        checkboxes_layout.addStretch(1)
        iface_layout.addWidget(checkboxes_row, iface_row, 0, 1, 3)
        iface_row += 1

        # Split top view toggle removed; tabs are always in a top splitter
        
        # Add stretch to push content to top
        iface_layout.setRowStretch(iface_row, 1)

        # tabs: Interface added later after all tabs are constructed

        # Config tab (available devices and layout selection)
        config_tab = QtWidgets.QWidget()
        cfg_layout = QtWidgets.QGridLayout(config_tab)
        cfg_row = 0

        # Layout selection (flat row, no group box outline)
        layout_row = QtWidgets.QWidget()
        layout_row_layout = QtWidgets.QHBoxLayout(layout_row)
        layout_row_layout.setContentsMargins(0, 0, 0, 0)
        layout_row_layout.setSpacing(6)
        self.rb_layout_single = QtWidgets.QRadioButton("Single Device")
        self.rb_layout_mound = QtWidgets.QRadioButton("Pitching Mound")
        self.rb_layout_single.setChecked(True)
        layout_row_layout.addWidget(self.rb_layout_single)
        layout_row_layout.addWidget(self.rb_layout_mound)
        layout_row_layout.addStretch(1)
        cfg_layout.addWidget(layout_row, cfg_row, 0, 1, 3)
        self.state.display_mode = "single"
        cfg_row += 1

        # Device filters
        filter_row = QtWidgets.QWidget()
        filter_row_layout = QtWidgets.QHBoxLayout(filter_row)
        filter_row_layout.setContentsMargins(0, 0, 0, 0)
        filter_row_layout.setSpacing(6)
        self.chk_filter_06 = QtWidgets.QCheckBox("Show 06 (Lite)")
        self.chk_filter_06.setChecked(True)
        self.chk_filter_07 = QtWidgets.QCheckBox("Show 07 (Launchpad)")
        self.chk_filter_07.setChecked(True)
        self.chk_filter_08 = QtWidgets.QCheckBox("Show 08 (XL)")
        self.chk_filter_08.setChecked(True)
        filter_row_layout.addWidget(self.chk_filter_06)
        filter_row_layout.addWidget(self.chk_filter_07)
        filter_row_layout.addWidget(self.chk_filter_08)
        filter_row_layout.addStretch(1)
        cfg_layout.addWidget(filter_row, cfg_row, 0, 1, 3)
        cfg_row += 1

        # Available devices list + refresh
        refresh_row = QtWidgets.QHBoxLayout()
        self.btn_refresh_devices = QtWidgets.QPushButton("Refresh Devices")
        refresh_row.addStretch(1)
        refresh_row.addWidget(self.btn_refresh_devices)
        cfg_layout.addLayout(refresh_row, cfg_row, 0, 1, 3)
        cfg_row += 1

        # Available devices list
        self.device_list = QtWidgets.QListWidget()
        self.device_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.device_list.setItemDelegate(DeviceListDelegate())
        cfg_layout.addWidget(self.device_list, cfg_row, 0, 1, 3)
        cfg_row += 1

        self._config_tab_index = tabs.addTab(config_tab, "Config")
        tabs.addTab(connection_tab, "Connection")
        tabs.addTab(interface_tab, "Interface")
        tabs.addTab(demo_tab, "Demo")

        # Add Tare button at the top
        tare_row = QtWidgets.QHBoxLayout()
        tare_row.addStretch(1)
        self.btn_tare = QtWidgets.QPushButton("Tare")
        _fix_btn(self.btn_tare, 110)
        tare_row.addWidget(self.btn_tare)
        root.addLayout(tare_row)
        
        # Backing store for devices
        self._all_devices: List[Tuple[str, str, str]] = []  # list of (name, axfId, type_code "06"|"07"|"08")

        root.addWidget(tabs)

        # Signals
        self.btn_connect.clicked.connect(self._emit_connect)
        self.btn_disconnect.clicked.connect(self.disconnect_requested.emit)
        self.btn_start.clicked.connect(self._emit_start)
        self.btn_stop.clicked.connect(self._emit_stop)
        self.btn_tare.clicked.connect(self._emit_tare)
        self.scale_slider.valueChanged.connect(self._on_scale)
        self.chk_plates.stateChanged.connect(self._on_flags)
        self.chk_labels.stateChanged.connect(self._on_flags)
        self.chk_filter_06.stateChanged.connect(self._on_filter_changed)
        self.chk_filter_07.stateChanged.connect(self._on_filter_changed)
        self.chk_filter_08.stateChanged.connect(self._on_filter_changed)
        self.rb_layout_mound.toggled.connect(self._on_layout_changed)
        self.rb_layout_single.toggled.connect(self._on_layout_changed)
        self.device_list.currentItemChanged.connect(self._on_device_selected)
        self.btn_refresh_devices.clicked.connect(lambda: self.refresh_devices_requested.emit())
        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _emit_connect(self) -> None:
        host = self.host_edit.text().strip() or config.SOCKET_HOST
        port = int(self.port_spin.value())
        self.connect_requested.emit(host, port)

    def _on_scale(self, value: int) -> None:
        self.state.cop_scale_k = max(0.01, value / 100.0)
        self.scale_label.setText(f"{self.state.cop_scale_k:.2f}")
        self.scale_changed.emit(self.state.cop_scale_k)

    def _on_flags(self) -> None:
        self.state.flags.show_plates = self.chk_plates.isChecked()
        # Leave markers as-is; control removed from UI
        self.state.flags.show_labels = self.chk_labels.isChecked()
        self.flags_changed.emit()

    # Config tab helpers
    def set_available_devices(self, devices: List[Tuple[str, str, str]]) -> None:
        # devices: List of (name, axfId, type_code)
        self._all_devices = devices or []
        self._populate_device_list()

    def _populate_device_list(self) -> None:
        show06 = self.chk_filter_06.isChecked()
        show07 = self.chk_filter_07.isChecked()
        show08 = self.chk_filter_08.isChecked()
        selected_id = self.state.selected_device_id or ""
        self.device_list.blockSignals(True)
        self.device_list.clear()
        for name, axf_id, dev_type in self._all_devices:
            if dev_type == "06" and not show06:
                continue
            if dev_type == "07" and not show07:
                continue
            if dev_type == "08" and not show08:
                continue
            display = f"{name} ({axf_id})"
            item = QtWidgets.QListWidgetItem(display)
            item.setData(QtCore.Qt.UserRole, (name, axf_id, dev_type))
            item.setData(QtCore.Qt.UserRole + 1, False)  # Initialize as not active
            self.device_list.addItem(item)
            if selected_id and axf_id == selected_id:
                self.device_list.setCurrentItem(item)
        self.device_list.blockSignals(False)
        self.device_list.setEnabled(self.rb_layout_single.isChecked())
    
    def update_active_devices(self, active_device_ids: set) -> None:
        """Update device list to show green check for active devices."""
        for i in range(self.device_list.count()):
            item = self.device_list.item(i)
            if item is None:
                continue
            try:
                name, axf_id, dev_type = item.data(QtCore.Qt.UserRole)
                # Check if device is active (match by normalized ID)
                is_active = any(axf_id in active_id or active_id in axf_id for active_id in active_device_ids)
                
                # Store active status in custom role for delegate to use
                item.setData(QtCore.Qt.UserRole + 1, is_active)
                
                # Keep text as just the device name
                display = f"{name} ({axf_id})"
                item.setText(display)
                item.setForeground(QtGui.QColor(255, 255, 255))
            except Exception:
                continue
        
        # Trigger repaint to update the checkmarks
        self.device_list.viewport().update()

    def _on_filter_changed(self) -> None:
        self._populate_device_list()

    def _on_layout_changed(self, _checked: bool) -> None:
        if self.rb_layout_mound.isChecked():
            self.state.display_mode = "mound"
            self.state.selected_device_id = None
            self.state.selected_device_type = None
        else:
            self.state.display_mode = "single"
        self._populate_device_list()
        self.config_changed.emit()

    def _on_device_selected(self, current: Optional[QtWidgets.QListWidgetItem], _previous: Optional[QtWidgets.QListWidgetItem]) -> None:
        if current is None:
            return
        try:
            name, axf_id, dev_type = current.data(QtCore.Qt.UserRole)
        except Exception:
            return
        self.state.selected_device_id = str(axf_id)
        self.state.selected_device_type = str(dev_type)
        self.state.selected_device_name = str(name)
        self.state.display_mode = "single"
        self.config_changed.emit()

    def _on_tab_changed(self, idx: int) -> None:
        try:
            if idx == getattr(self, "_config_tab_index", -1):
                self.refresh_devices_requested.emit()
        except Exception:
            pass

    def _emit_start(self) -> None:
        payload = {
            "capture_name": "",
            "capture_configuration": self.capture_type.currentText() or "pitch",
            "group_id": self.group_edit.text().strip(),
            "athlete_id": self.athlete_edit.text().strip(),
        }
        self.start_capture_requested.emit(payload)

    def _emit_stop(self) -> None:
        payload = {"group_id": self.group_edit.text().strip()}
        self.stop_capture_requested.emit(payload)

    def _emit_tare(self) -> None:
        gid = self.group_edit.text().strip()
        self.tare_requested.emit(gid)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AxioforceFluxLite")

        # Bridge for thread-safe UI updates
        self.bridge = UiBridge()

        self.state = ViewState()
        # Create canvases for left/right views (both receive updates)
        self.canvas_left = WorldCanvas(self.state)
        self.canvas_right = WorldCanvas(self.state)
        # Back-compat alias
        self.canvas = self.canvas_left
        self.controls = ControlPanel(self.state)

        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        # Top area: tabbed views with optional horizontal split
        self.top_tabs_left = QtWidgets.QTabWidget()
        self.top_tabs_right = QtWidgets.QTabWidget()

        # Left tabs
        self.top_tabs_left.addTab(self.canvas_left, "Plate View")
        sensor_left = QtWidgets.QWidget()
        sll = QtWidgets.QVBoxLayout(sensor_left)
        sll.setContentsMargins(0, 0, 0, 0)
        lbl_sens_l = QtWidgets.QLabel("Sensor View (coming soon)")
        lbl_sens_l.setAlignment(QtCore.Qt.AlignCenter)
        sll.addStretch(1)
        sll.addWidget(lbl_sens_l)
        sll.addStretch(1)
        self.top_tabs_left.addTab(sensor_left, "Sensor View")
        force_left = QtWidgets.QWidget()
        fll = QtWidgets.QVBoxLayout(force_left)
        fll.setContentsMargins(0, 0, 0, 0)
        self.force_plot_left = ForcePlotWidget()
        fll.addWidget(self.force_plot_left)
        self.top_tabs_left.addTab(force_left, "Force View")

        # Right tabs
        self.top_tabs_right.addTab(self.canvas_right, "Plate View")
        sensor_right = QtWidgets.QWidget()
        srl = QtWidgets.QVBoxLayout(sensor_right)
        srl.setContentsMargins(0, 0, 0, 0)
        lbl_sens_r = QtWidgets.QLabel("Sensor View (coming soon)")
        lbl_sens_r.setAlignment(QtCore.Qt.AlignCenter)
        srl.addStretch(1)
        srl.addWidget(lbl_sens_r)
        srl.addStretch(1)
        self.top_tabs_right.addTab(sensor_right, "Sensor View")
        force_right = QtWidgets.QWidget()
        frl = QtWidgets.QVBoxLayout(force_right)
        frl.setContentsMargins(0, 0, 0, 0)
        self.force_plot_right = ForcePlotWidget()
        frl.addWidget(self.force_plot_right)
        self.top_tabs_right.addTab(force_right, "Force View")
        # Default to Sensor tab on the right
        self.top_tabs_right.setCurrentWidget(sensor_right)

        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.splitter.addWidget(self.top_tabs_left)
        self.splitter.addWidget(self.top_tabs_right)

        # Always show a horizontal splitter with two tab stacks; tabs are draggable
        self.top_tabs_left.setMovable(True)
        self.top_tabs_right.setMovable(True)
        layout.addWidget(self.splitter)
        layout.addWidget(self.controls)
        # Make the bottom settings section larger
        # Let the window be resizable and allocate 3/5 canvas, 2/5 controls
        self.controls.setMinimumHeight(220)
        layout.setStretch(0, 3)  # canvas
        layout.setStretch(1, 2)  # controls
        # Ensure Plate View tab is active on the left
        self.top_tabs_left.setCurrentWidget(self.canvas_left)
        # Provide reasonable initial splitter sizes after the window shows
        try:
            self.splitter.setStretchFactor(0, 1)
            self.splitter.setStretchFactor(1, 1)
            QtCore.QTimer.singleShot(0, lambda: self.splitter.setSizes([800, 800]))
        except Exception:
            pass
        self.setCentralWidget(central)

        # Status bar
        self.status_label = QtWidgets.QLabel("Disconnected")
        self.rate_label = QtWidgets.QLabel("Hz: --")
        self.statusBar().addPermanentWidget(self.status_label)
        self.statusBar().addPermanentWidget(self.rate_label)

        # React to config changes by redrawing canvas
        self.controls.config_changed.connect(self._on_config_changed)
        # Discovery refresh
        self.controls.refresh_devices_requested.connect(self._on_refresh_devices)
        
        # Wire canvas mound device selection (both canvases)
        self.canvas_left.mound_device_selected.connect(self._on_mound_device_selected)
        self.canvas_right.mound_device_selected.connect(self._on_mound_device_selected)

        # Auto-refresh devices shortly after startup
        QtCore.QTimer.singleShot(500, lambda: self.controls.refresh_devices_requested.emit())

        # Bridge signal connections (queued to UI thread)
        self.bridge.snapshots_ready.connect(self.on_snapshots)
        self.bridge.connection_text_ready.connect(self.set_connection_text)
        self.bridge.single_snapshot_ready.connect(self.canvas_left.set_single_snapshot)
        self.bridge.single_snapshot_ready.connect(self.canvas_right.set_single_snapshot)
        self.bridge.plate_device_id_ready.connect(self.set_plate_device_id)
        self.bridge.available_devices_ready.connect(self.set_available_devices)
        self.bridge.active_devices_ready.connect(self.update_active_devices)
        self.bridge.force_vector_ready.connect(self._on_force_vector)

    # Controller hooks
    def on_snapshots(self, snaps: Dict[str, Tuple[float, float, float, int, bool, float, float]], hz_text: Optional[str]) -> None:
        if hz_text:
            self.rate_label.setText(hz_text)
        self.canvas_left.set_snapshots(snaps)
        self.canvas_right.set_snapshots(snaps)

    def set_connection_text(self, txt: str) -> None:
        self.status_label.setText(txt)

    def on_connect_clicked(self, slot: Callable[[str, int], None]) -> None:
        self.controls.connect_requested.connect(lambda h, p: slot(h, p))

    def on_disconnect_clicked(self, slot: Callable[[], None]) -> None:
        self.controls.disconnect_requested.connect(slot)

    def on_flags_changed(self, slot: Callable[[], None]) -> None:
        self.controls.flags_changed.connect(slot)

    def on_start_capture(self, slot: Callable[[dict], None]) -> None:
        self.controls.start_capture_requested.connect(lambda payload: slot(payload))

    def on_stop_capture(self, slot: Callable[[dict], None]) -> None:
        self.controls.stop_capture_requested.connect(lambda payload: slot(payload))

    def on_tare(self, slot: Callable[[str], None]) -> None:
        self.controls.tare_requested.connect(lambda gid: slot(gid))

    # Config hooks
    def on_config_changed(self, slot: Callable[[], None]) -> None:
        self.controls.config_changed.connect(slot)

    def set_available_devices(self, devices: List[Tuple[str, str]]) -> None:
        self.controls.set_available_devices(devices)
        # Also pass to canvas for device picker
        self.canvas_left.set_available_devices(devices)
        self.canvas_right.set_available_devices(devices)
    
    def update_active_devices(self, active_device_ids: set) -> None:
        """Forward active device updates to control panel and canvas."""
        self.controls.update_active_devices(active_device_ids)
        self.canvas_left.update_active_devices(active_device_ids)
        self.canvas_right.update_active_devices(active_device_ids)

    def _on_config_changed(self) -> None:
        # Refresh canvases
        self.canvas_left._fit_done = False
        self.canvas_right._fit_done = False
        self.canvas_left.update()
        self.canvas_right.update()
        # Clear force plots when config changes (e.g., device selection)
        try:
            self.force_plot_left.clear()
            self.force_plot_right.clear()
        except Exception:
            pass
        # Dynamic split toggling removed
    
    def _on_mound_device_selected(self, position_id: str, device_id: str) -> None:
        """Handle mound device selection from canvas."""
        if hasattr(self, "_on_mound_device_cb") and callable(self._on_mound_device_cb):
            try:
                self._on_mound_device_cb(position_id, device_id)
            except Exception:
                pass
    
    def on_mound_device_selected(self, slot: Callable[[str, str], None]) -> None:
        """Hook for controller to wire mound device selection."""
        self._on_mound_device_cb = slot

    def on_request_discovery(self, slot: Callable[[], None]) -> None:
        # Hook for controller to wire a discovery trigger
        self._on_refresh_cb = slot

    def _on_refresh_devices(self) -> None:
        try:
            if hasattr(self, "_on_refresh_cb") and callable(self._on_refresh_cb):
                self._on_refresh_cb()
        except Exception:
            pass

    # Track last-seen device id per plate for labeling
    def set_plate_device_id(self, plate_name: str, device_id: str) -> None:
        self.state.plate_device_ids[plate_name] = device_id

    # Force plotting: route data to both plots; they can filter later if needed
    def _on_force_vector(self, device_id: str, t_ms: int, fx: float, fy: float, fz: float) -> None:
        try:
            # For now, plot whatever comes in on both plots
            if hasattr(self, "force_plot_left") and self.force_plot_left is not None:
                self.force_plot_left.add_point(t_ms, fx, fy, fz)
            if hasattr(self, "force_plot_right") and self.force_plot_right is not None:
                self.force_plot_right.add_point(t_ms, fx, fy, fz)
        except Exception:
            pass


