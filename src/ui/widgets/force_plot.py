from __future__ import annotations

from typing import Optional, Tuple, Dict

from PySide6 import QtCore, QtGui, QtWidgets

from ... import config


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

    def paintEvent(self, _e: QtGui.QPaintEvent) -> None:  # noqa: N802
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QtGui.QColor(*config.COLOR_BG))
        m_left, m_right, m_top, m_bottom = 36, 12, 6, 18
        x0, y0 = m_left, m_top
        pw, ph = max(1, w - m_left - m_right), max(1, h - m_top - m_bottom)
        axis_pen = QtGui.QPen(QtGui.QColor(180, 180, 180))
        axis_pen.setWidth(1)
        p.setPen(axis_pen)
        p.drawRect(x0, y0, pw, ph)
        if self._y_min < 0 < self._y_max:
            zy = int(y0 + ph * (1 - (0 - self._y_min) / (self._y_max - self._y_min)))
            p.drawLine(x0, zy, x0 + pw, zy)
        if not self._samples:
            p.end()
            return
        n = len(self._samples)

        def to_xy(i: int, v: float) -> tuple[int, int]:
            x = x0 + int(pw * (i / max(1, self._max_points - 1)))
            y = y0 + int(ph * (1 - (v - self._y_min) / max(1e-6, (self._y_max - self._y_min))))
            return x, y

        pen_x = QtGui.QPen(QtGui.QColor(220, 80, 80))
        pen_y = QtGui.QPen(QtGui.QColor(80, 180, 220))
        pen_z = QtGui.QPen(QtGui.QColor(120, 220, 120))
        pen_x.setWidth(2)
        pen_y.setWidth(2)
        pen_z.setWidth(2)
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
        p.setPen(QtGui.QColor(200, 200, 200))
        p.drawText(x0 + 6, y0 + 14, "ΣFx")
        p.drawText(x0 + 46, y0 + 14, "ΣFy")
        p.drawText(x0 + 86, y0 + 14, "ΣFz")
        p.end()


