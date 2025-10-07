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
        # Legend toggles for mound dual-series mode (overlay in plot area, top-right)
        self._dual_enabled = False
        self._legend_launch = QtWidgets.QCheckBox("Launch")
        self._legend_landing = QtWidgets.QCheckBox("Landing")
        for cb in (self._legend_launch, self._legend_landing):
            cb.setChecked(True)
            cb.setVisible(True)
            try:
                cb.setCursor(QtCore.Qt.PointingHandCursor)
            except Exception:
                pass
            cb.stateChanged.connect(lambda _v: self.update())
        self._legend_container = QtWidgets.QWidget(self)
        _ll = QtWidgets.QHBoxLayout(self._legend_container)
        _ll.setContentsMargins(6, 2, 6, 2)
        _ll.setSpacing(6)
        _ll.addWidget(self._legend_launch)
        _ll.addWidget(self._legend_landing)
        self._legend_container.setVisible(False)
        # Subtle dark background to remain readable over grid
        self._legend_container.setStyleSheet(
            "background: rgba(30,30,35,140); border: 1px solid rgba(200,200,200,60); border-radius: 4px;"
        )
        root.addLayout(hdr)
        self._samples: list[tuple[int, float, float, float]] = []  # (t_ms, fx, fy, fz) single-device mode
        self._samples_launch: list[tuple[int, float, float, float]] = []
        self._samples_landing: list[tuple[int, float, float, float]] = []
        self._max_points = 600  # ~10s at 60 Hz
        self._auto_scale = True
        self._y_min = -10.0
        self._y_max = 10.0

    def _recompute_autoscale(self) -> None:
        if not self._auto_scale:
            return
        try:
            # Determine which data are currently visible (last _max_points)
            def max_abs_from(samples: list[tuple[int, float, float, float]]) -> float:
                if not samples:
                    return 0.0
                i0 = max(0, len(samples) - self._max_points)
                peak = 0.0
                for i in range(i0, len(samples)):
                    _, fx, fy, fz = samples[i]
                    # Consider all components; scale to the maximum absolute value
                    if abs(fx) > peak:
                        peak = abs(fx)
                    if abs(fy) > peak:
                        peak = abs(fy)
                    if abs(fz) > peak:
                        peak = abs(fz)
                return peak

            if self._dual_enabled:
                # Respect legend toggles for visibility
                peaks: list[float] = []
                if self._legend_launch.isChecked():
                    peaks.append(max_abs_from(self._samples_launch))
                if self._legend_landing.isChecked():
                    peaks.append(max_abs_from(self._samples_landing))
                peak = max(peaks) if peaks else 0.0
            else:
                peak = max_abs_from(self._samples)

            # Add a comfortable headroom; enforce a reasonable minimum
            target = max(peak * 1.15, 5.0)
            # Smooth changes to avoid flicker
            new_max = max(target, self._y_max * 0.8 + target * 0.2)
            self._y_max = new_max
            self._y_min = -new_max
        except Exception:
            pass

    def clear(self) -> None:
        self._samples.clear()
        self._samples_launch.clear()
        self._samples_landing.clear()
        # Reset scale
        self._y_min = -10.0
        self._y_max = 10.0
        self.update()

    def add_point(self, t_ms: int, fx: float, fy: float, fz: float) -> None:
        self._samples.append((t_ms, fx, fy, fz))
        if len(self._samples) > self._max_points:
            self._samples = self._samples[-self._max_points:]
        self._recompute_autoscale()
        self.update()

    # Dual-series API for mound mode
    def set_dual_series_enabled(self, enabled: bool) -> None:
        self._dual_enabled = bool(enabled)
        self._legend_container.setVisible(self._dual_enabled)
        self.update()

    def add_point_launch(self, t_ms: int, fx: float, fy: float, fz: float) -> None:
        self._samples_launch.append((t_ms, fx, fy, fz))
        if len(self._samples_launch) > self._max_points:
            self._samples_launch = self._samples_launch[-self._max_points:]
        self._recompute_autoscale()
        self.update()

    def add_point_landing(self, t_ms: int, fx: float, fy: float, fz: float) -> None:
        self._samples_landing.append((t_ms, fx, fy, fz))
        if len(self._samples_landing) > self._max_points:
            self._samples_landing = self._samples_landing[-self._max_points:]
        self._recompute_autoscale()
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
        # Position legend toggles at top-right inside grid when enabled
        try:
            if self._dual_enabled and hasattr(self, "_legend_container") and self._legend_container is not None:
                self._legend_container.adjustSize()
                sz = self._legend_container.sizeHint()
                cx = max(x0, x0 + pw - sz.width() - 6)
                cy = max(y0, y0 + 6)
                self._legend_container.setGeometry(cx, cy, sz.width(), sz.height())
                self._legend_container.setVisible(True)
            elif hasattr(self, "_legend_container") and self._legend_container is not None:
                self._legend_container.setVisible(False)
        except Exception:
            pass
        # One last autoscale just before drawing, so axes reflect current on-screen peaks
        self._recompute_autoscale()
        if self._y_min < 0 < self._y_max:
            zy = int(y0 + ph * (1 - (0 - self._y_min) / (self._y_max - self._y_min)))
            p.drawLine(x0, zy, x0 + pw, zy)
        # Determine drawing mode
        draw_dual = self._dual_enabled and (self._samples_launch or self._samples_landing)
        if not draw_dual and not self._samples:
            p.end()
            return

        def to_xy(i: int, v: float) -> tuple[int, int]:
            x = x0 + int(pw * (i / max(1, self._max_points - 1)))
            y = y0 + int(ph * (1 - (v - self._y_min) / max(1e-6, (self._y_max - self._y_min))))
            return x, y

        # Base axis colors
        base_x = QtGui.QColor(220, 80, 80)
        base_y = QtGui.QColor(80, 180, 220)
        base_z = QtGui.QColor(120, 220, 120)

        def make_pen(c: QtGui.QColor) -> QtGui.QPen:
            pen = QtGui.QPen(c)
            pen.setWidth(2)
            return pen

        if not draw_dual:
            n = len(self._samples)
            pen_x = make_pen(base_x)
            pen_y = make_pen(base_y)
            pen_z = make_pen(base_z)
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
        else:
            # Dual-series overlay: Launch (base colors) and Landing (lighter variants)
            # Lighter colors for landing
            land_x = QtGui.QColor(255, 140, 140)
            land_y = QtGui.QColor(140, 220, 255)
            land_z = QtGui.QColor(160, 255, 160)

            series = []
            if self._legend_launch.isChecked() and self._samples_launch:
                series.append((self._samples_launch, make_pen(base_x), 1))
                series.append((self._samples_launch, make_pen(base_y), 2))
                series.append((self._samples_launch, make_pen(base_z), 3))
            if self._legend_landing.isChecked() and self._samples_landing:
                series.append((self._samples_landing, make_pen(land_x), 1))
                series.append((self._samples_landing, make_pen(land_y), 2))
                series.append((self._samples_landing, make_pen(land_z), 3))

            # Draw each series path
            for samples, pen, comp in series:
                n = len(samples)
                p.setPen(pen)
                path = QtGui.QPainterPath()
                i0 = max(0, n - self._max_points)
                for i in range(i0, n):
                    t_ms, fx, fy, fz = samples[i]
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


