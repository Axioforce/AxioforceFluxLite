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

    def set_grid(self, rows: int, cols: int) -> None:
        self.rows = max(1, int(rows))
        self.cols = max(1, int(cols))
        self.update()

    def set_plate_rect_px(self, rect: QtCore.QRect) -> None:
        self._plate_rect_px = QtCore.QRect(rect)
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
                box_w = int(rect.width() * 0.8)
                box_h = int(rect.height() * 0.22)
                box_x = rect.left() + (rect.width() - box_w) // 2
                box_y = rect.top() + (rect.height() - box_h) // 2
                status_rect = QtCore.QRect(box_x, box_y, box_w, box_h)
                # Background
                bg = QtGui.QColor(0, 0, 0, 140)
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(bg)
                p.drawRoundedRect(status_rect, 10, 10)
                # Text
                p.setPen(QtGui.QColor(255, 255, 255))
                font = p.font()
                font.setPointSize(max(10, int(min(rect.width(), rect.height()) * 0.035)))
                font.setBold(True)
                p.setFont(font)
                flags = QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap
                p.drawText(status_rect.adjusted(12, 8, -12, -8), flags, self._status_text)
            except Exception:
                pass
        p.end()


