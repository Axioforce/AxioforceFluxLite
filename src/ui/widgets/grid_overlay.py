from __future__ import annotations

from typing import Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets


class GridOverlay(QtWidgets.QWidget):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.rows = 3
        self.cols = 3
        self.active_cell: Optional[Tuple[int, int]] = None
        self.cell_colors: dict[Tuple[int, int], QtGui.QColor] = {}
        self._plate_rect_px: Optional[QtCore.QRect] = None
        self._status_text: Optional[str] = None
        # Optional mode: draw a single central circle instead of a full grid
        self._center_circle_mode: bool = False
        self._center_circle_radius_px: Optional[int] = None

    def set_grid(self, rows: int, cols: int) -> None:
        self.rows = max(1, int(rows))
        self.cols = max(1, int(cols))
        self.update()

    def set_plate_rect_px(self, rect: QtCore.QRect) -> None:
        self._plate_rect_px = QtCore.QRect(rect)
        self.update()

    def set_center_circle_mode(self, enabled: bool) -> None:
        """Enable/disable single central circle rendering (used for discrete temp testing)."""
        self._center_circle_mode = bool(enabled)
        self.update()

    def is_center_circle_mode(self) -> bool:
        """Return True when the overlay is in single-center-circle (discrete temp) mode."""
        return bool(self._center_circle_mode)

    def set_center_circle_radius_px(self, radius_px: Optional[int]) -> None:
        """Set desired center-circle radius in pixels (approx 5 cm on plate)."""
        try:
            r = int(radius_px) if radius_px is not None else 0
        except Exception:
            r = 0
        self._center_circle_radius_px = r if r > 0 else None
        self.update()

    def set_active_cell(self, row: Optional[int], col: Optional[int]) -> None:
        if row is None or col is None:
            self.active_cell = None
        else:
            self.active_cell = (int(row), int(col))
        self.update()

    def set_cell_color(self, row: int, col: int, color: QtGui.QColor) -> None:
        self.cell_colors[(int(row), int(col))] = color
        self.update()

    def clear_cell_color(self, row: int, col: int) -> None:
        try:
            key = (int(row), int(col))
            if key in self.cell_colors:
                self.cell_colors.pop(key, None)
                self.update()
        except Exception:
            pass

    def clear_colors(self) -> None:
        self.cell_colors.clear()
        self.update()

    def set_status(self, text: Optional[str]) -> None:
        self._status_text = (text or "").strip() or None
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        if self._plate_rect_px is None:
            return
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = self._plate_rect_px
        cell_w = rect.width() / max(1, self.cols)
        cell_h = rect.height() / max(1, self.rows)

        if self._center_circle_mode and self.rows == 1 and self.cols == 1:
            # Single-center-circle mode: draw one circular target instead of a grid
            cx = rect.left() + rect.width() // 2
            cy = rect.top() + rect.height() // 2
            # Base radius from physical sizing (passed in as px), clamped to plate size
            if self._center_circle_radius_px is not None:
                radius = int(self._center_circle_radius_px)
            else:
                radius = int(min(cell_w, cell_h) * 0.2)
            max_radius = int(min(cell_w, cell_h) * 0.45)
            if radius > max_radius:
                radius = max_radius
            # Fill if a color has been set for the single "cell"
            color = self.cell_colors.get((0, 0))
            if color is not None:
                p.setBrush(color)
            else:
                p.setBrush(QtCore.Qt.NoBrush)
            # Outline for target
            outline = QtGui.QColor(200, 200, 220, 220)
            p.setPen(QtGui.QPen(outline, 2))
            p.drawEllipse(QtCore.QPoint(cx, cy), radius, radius)
            # Active highlight: slightly thicker ring
            if self.active_cell is not None:
                try:
                    ar, ac = self.active_cell
                    if int(ar) == 0 and int(ac) == 0:
                        p.setBrush(QtCore.Qt.NoBrush)
                        p.setPen(QtGui.QPen(QtGui.QColor(0, 200, 255, 180), 3))
                        p.drawEllipse(QtCore.QPoint(cx, cy), radius + 4, radius + 4)
                except Exception:
                    pass
        else:
            # Fill cells with colors if any
            for r in range(self.rows):
                for c in range(self.cols):
                    color = self.cell_colors.get((r, c))
                    if color is not None:
                        cell_rect = QtCore.QRect(
                            int(rect.left() + c * cell_w),
                            int(rect.top() + r * cell_h),
                            int(cell_w),
                            int(cell_h),
                        )
                        p.fillRect(cell_rect, color)

            # Draw grid lines
            p.setPen(QtGui.QPen(QtGui.QColor(180, 180, 180, 180), 1))
            for i in range(1, self.cols):
                x = rect.left() + int(i * cell_w)
                p.drawLine(x, rect.top(), x, rect.bottom())
            for j in range(1, self.rows):
                y = rect.top() + int(j * cell_h)
                p.drawLine(rect.left(), y, rect.right(), y)

            # Active cell highlight
            if self.active_cell is not None:
                r, c = self.active_cell
                highlight = QtGui.QColor(0, 200, 255, 90)
                p.setBrush(highlight)
                p.setPen(QtGui.QPen(QtGui.QColor(0, 180, 240), 2))
                active_rect = QtCore.QRect(
                    int(rect.left() + c * cell_w),
                    int(rect.top() + r * cell_h),
                    int(cell_w),
                    int(cell_h),
                )
                p.drawRect(active_rect)

        # Status box overlay
        if self._status_text:
            try:
                # Place a readable box to the right side of the plate rect, within widget bounds
                margin = 12
                max_w = max(240, int(self.width() * 0.35))
                box_w = min(max_w, max(240, int(rect.width() * 0.6)))
                box_h = max(60, int(rect.height() * 0.26))
                box_x = min(self.width() - box_w - margin, rect.right() + margin)
                # Align vertically to middle of plate rect
                box_y = max(margin, min(self.height() - box_h - margin, rect.top() + (rect.height() - box_h) // 2))
                status_rect = QtCore.QRect(int(box_x), int(box_y), int(box_w), int(box_h))
                # Background
                bg = QtGui.QColor(0, 0, 0, 140)
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(bg)
                p.drawRoundedRect(status_rect, 10, 10)
                # Text
                p.setPen(QtGui.QColor(255, 255, 255))
                font = p.font()
                font.setPointSize(11)
                font.setBold(True)
                p.setFont(font)
                flags = QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap
                p.drawText(status_rect.adjusted(10, 8, -10, -8), flags, self._status_text)
            except Exception:
                pass
        p.end()


