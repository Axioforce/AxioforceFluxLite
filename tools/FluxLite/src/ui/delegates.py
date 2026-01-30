from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets


class DeviceListDelegate(QtWidgets.QStyledItemDelegate):
    """Custom delegate to render green checkmark for active devices."""

    def paint(self, painter: QtGui.QPainter, option: QtWidgets.QStyleOptionViewItem, index: QtCore.QModelIndex) -> None:  # noqa: N802
        super().paint(painter, option, index)
        is_active = index.data(QtCore.Qt.UserRole + 1)
        if is_active:
            painter.save()
            rect = option.rect
            text = index.data(QtCore.Qt.DisplayRole)
            check_text = " ✓"
            painter.setFont(option.font)
            fm = painter.fontMetrics()
            text_width = fm.horizontalAdvance(text)
            x = rect.left() + text_width + 5
            y = rect.center().y() + fm.ascent() // 2
            painter.setPen(QtGui.QColor(100, 200, 100))
            painter.drawText(x, y, check_text)
            painter.restore()


class DiscreteTestDelegate(QtWidgets.QStyledItemDelegate):
    """Custom delegate to render discrete tests with left label, dotted leader, and right-aligned date."""

    def paint(self, painter: QtGui.QPainter, option: QtWidgets.QStyleOptionViewItem, index: QtCore.QModelIndex) -> None:  # type: ignore[override]
        # Let base class draw background/selection
        QtWidgets.QStyledItemDelegate.paint(self, painter, option, index)
        left = str(index.data(QtCore.Qt.UserRole + 1) or "")
        date = str(index.data(QtCore.Qt.UserRole + 2) or "")
        if not left and not date:
            return
        r = option.rect
        fm = option.fontMetrics
        painter.save()
        painter.setPen(option.palette.color(QtGui.QPalette.Text))
        # Vertical alignment
        baseline_y = r.top() + (r.height() + fm.ascent() - fm.descent()) // 2
        padding = 6
        left_x = r.left() + padding
        # Draw left label
        painter.drawText(left_x, baseline_y, left)
        left_w = fm.horizontalAdvance(left)
        # Draw right date
        date_w = fm.horizontalAdvance(date)
        right_x = r.right() - padding - date_w
        painter.drawText(right_x, baseline_y, date)
        # Draw dotted leader between
        dots_start_x = left_x + left_w + padding
        dots_end_x = right_x - padding
        dot_w = max(1, fm.horizontalAdvance("."))
        if dots_end_x > dots_start_x + dot_w:
            count = int((dots_end_x - dots_start_x) / dot_w)
            dots = "." * max(0, count)
            painter.drawText(dots_start_x, baseline_y, dots)
        painter.restore()
