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


