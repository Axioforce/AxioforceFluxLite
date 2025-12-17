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
        # Header removed (no title text on the plot)
        hdr = QtWidgets.QHBoxLayout()
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
            cb.stateChanged.connect(self._on_legend_toggle_change)
        self._legend_container = QtWidgets.QWidget(self)
        _ll = QtWidgets.QHBoxLayout(self._legend_container)
        _ll.setContentsMargins(6, 2, 6, 2)
        _ll.setSpacing(6)
        _ll.addWidget(self._legend_launch)
        _ll.addWidget(self._legend_landing)
        self._legend_container.setVisible(False)
        # Place toggles and temperature label in a top bar, right-aligned
        header_bar = QtWidgets.QHBoxLayout()
        header_bar.setContentsMargins(0, 0, 0, 0)
        header_bar.setSpacing(6)
        header_spacer = QtWidgets.QSpacerItem(0, 0, QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum)
        header_bar.addItem(header_spacer)
        header_bar.addWidget(self._legend_container)
        # Smoothed temperature label (used by Sensor View in right pane)
        self._temp_label = QtWidgets.QLabel("Temp: -- °F")
        self._temp_label.setStyleSheet("color: rgb(220,220,230); font-size: 13px;")
        header_bar.addWidget(self._temp_label)
        root.addLayout(header_bar)
        # Backend selection (pyqtgraph when enabled; fallback to painter)
        self._use_pg: bool = False
        self._pg = None  # type: ignore[assignment]
        self._plot_widget: Optional[QtWidgets.QWidget] = None
        if bool(getattr(config, "USE_PYQTGRAPH_FORCE_PLOT", False)):
            try:
                import pyqtgraph as pg  # type: ignore[import-not-found]
                self._pg = pg
                self._use_pg = True
            except Exception:
                self._use_pg = False
                self._pg = None
        # No header added to the layout to keep plot minimal
        self._samples: list[tuple[int, float, float, float]] = []  # (t_ms, fx, fy, fz) single-device mode
        self._samples_launch: list[tuple[int, float, float, float]] = []
        self._samples_landing: list[tuple[int, float, float, float]] = []
        self._max_points = 600  # ~10s at 60 Hz
        self._auto_scale = True
        self._y_min = -10.0
        self._y_max = 10.0
        # Pyqtgraph curves and buffers when using pg
        self._pg_curves: Dict[str, object] = {}
        self._pg_x_single: list[int] = []
        self._pg_fx: list[float] = []
        self._pg_fy: list[float] = []
        self._pg_fz: list[float] = []
        self._pg_x_launch: list[int] = []
        self._pg_lx: list[float] = []
        self._pg_ly: list[float] = []
        self._pg_lz: list[float] = []
        self._pg_x_land: list[int] = []
        self._pg_rx: list[float] = []
        self._pg_ry: list[float] = []
        self._pg_rz: list[float] = []
        # Time zero for relative axis formatting (ms)
        self._time0_ms: Optional[int] = None
        
        # Temperature buffer for 10-second rolling average
        self._temp_buffer: list[tuple[float, float]] = [] # (time_sec, temp_f)
        self._last_temp_update_time = 0.0
        
        if self._use_pg and self._pg is not None:
            pg = self._pg
            # Custom bottom axis formatter: show HR:MIN:SEC where X values are milliseconds
            try:
                widget_self = self
                class _HMSAxis(pg.AxisItem):  # type: ignore[name-defined]
                    def tickStrings(self, values, scale, spacing):  # type: ignore[override]
                        out = []
                        for v in values:
                            try:
                                base = widget_self._time0_ms or 0
                                rel_ms = float(v) - float(base)
                                if rel_ms < 0:
                                    rel_ms = 0.0
                                total_s = int(round(rel_ms / 1000.0))
                            except Exception:
                                total_s = 0
                            h = total_s // 3600
                            m = (total_s % 3600) // 60
                            s = total_s % 60
                            out.append(f"{h:d}:{m:02d}:{s:02d}")
                        return out
                axis_items = {"bottom": _HMSAxis(orientation="bottom")}  # type: ignore[arg-type]
            except Exception:
                axis_items = None
            if axis_items is not None:
                self._plot_widget = pg.PlotWidget(axisItems=axis_items, background=tuple(getattr(config, "COLOR_BG", (18, 18, 20))))
            else:
                self._plot_widget = pg.PlotWidget(background=tuple(getattr(config, "COLOR_BG", (18, 18, 20))))
            try:
                self._plot_widget.showGrid(x=False, y=True, alpha=0.3)  # type: ignore[attr-defined]
            except Exception:
                pass
            try:
                self._plot_widget.addLegend()  # type: ignore[attr-defined]
            except Exception:
                pass
            root.addWidget(self._plot_widget)
            def mkcurve(color: tuple[int, int, int], name: str):
                return self._plot_widget.plot(  # type: ignore[attr-defined]
                    pen=pg.mkPen(color=color, width=2), name=name, clipToView=True, autoDownsample=True
                )
            # Single
            self._pg_curves["fx"] = mkcurve((220, 80, 80), "Fx")
            self._pg_curves["fy"] = mkcurve((80, 180, 220), "Fy")
            self._pg_curves["fz"] = mkcurve((120, 220, 120), "Fz")
            # Dual (launch: base; landing: lighter)
            self._pg_curves["lx"] = mkcurve((220, 80, 80), "Launch Fx")
            self._pg_curves["ly"] = mkcurve((80, 180, 220), "Launch Fy")
            self._pg_curves["lz"] = mkcurve((120, 220, 120), "Launch Fz")
            self._pg_curves["rx"] = mkcurve((255, 140, 140), "Landing Fx")
            self._pg_curves["ry"] = mkcurve((140, 220, 255), "Landing Fy")
            self._pg_curves["rz"] = mkcurve((160, 255, 160), "Landing Fz")
            for key in ("lx", "ly", "lz", "rx", "ry", "rz"):
                try:
                    self._pg_curves[key].setVisible(False)  # type: ignore[union-attr]
                except Exception:
                    pass
            try:
                self._plot_widget.enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)  # type: ignore[attr-defined]
            except Exception:
                pass
        # Smoothed running values for visual stability
        self._ema_fx: Optional[float] = None
        self._ema_fy: Optional[float] = None
        self._ema_fz: Optional[float] = None
        self._ema_fx_launch: Optional[float] = None
        self._ema_fy_launch: Optional[float] = None
        self._ema_fz_launch: Optional[float] = None
        self._ema_fx_landing: Optional[float] = None
        self._ema_fy_landing: Optional[float] = None
        self._ema_fz_landing: Optional[float] = None
        # Smoothing toggle (affects plotted lines and overlay values)
        self._smoothing_enabled: bool = True
        # Last raw/smoothed single values (for overlay)
        self._last_raw_single: Optional[Tuple[float, float, float]] = None
        self._last_smoothed_single: Optional[Tuple[float, float, float]] = None
        # Autoscale damping (recompute every N frames)
        self._autoscale_damp_enabled: bool = bool(getattr(config, "PLOT_AUTOSCALE_DAMP_ENABLED", True))
        self._autoscale_every_n: int = int(max(1, getattr(config, "PLOT_AUTOSCALE_DAMP_EVERY_N", 2)))
        self._autoscale_counter: int = 0

        # Bottom-left overlay for current readings and Smooth toggle
        self._value_container = QtWidgets.QWidget(self)
        vl = QtWidgets.QHBoxLayout(self._value_container)
        vl.setContentsMargins(10, 6, 10, 6)
        vl.setSpacing(12)
        self.lbl_fx = QtWidgets.QLabel("Fx: --")
        self.lbl_fy = QtWidgets.QLabel("Fy: --")
        self.lbl_fz = QtWidgets.QLabel("Fz: --")
        for lab in (self.lbl_fx, self.lbl_fy, self.lbl_fz):
            lab.setStyleSheet("color: rgb(220,220,230); font-size: 15px; font-weight: 600;")
            # Fixed-size boxes to prevent layout jitter
            try:
                lab.setAlignment(QtCore.Qt.AlignCenter)
            except Exception:
                pass
            lab.setFixedWidth(88)
            lab.setFixedHeight(26)
        self.chk_smooth = QtWidgets.QCheckBox("Smooth")
        self.chk_smooth.setChecked(True)
        self.chk_smooth.setStyleSheet("color: rgb(220,220,230); font-size: 13px;")
        try:
            self.chk_smooth.setCursor(QtCore.Qt.PointingHandCursor)
        except Exception:
            pass
        self.chk_smooth.toggled.connect(self._on_smooth_toggled)
        vl.addWidget(self.lbl_fx)
        vl.addWidget(self.lbl_fy)
        vl.addWidget(self.lbl_fz)
        vl.addWidget(self.chk_smooth)
        self._value_container.setVisible(False)
        self._value_container.setStyleSheet(
            "background: rgba(30,30,35,160); border: 1px solid rgba(200,200,200,90); border-radius: 6px;"
        )
        # Keep our line toggles in the top bar for both backends

    def _pg_trim(self, xs: list[int], ys: list[float]) -> None:
        if len(xs) > self._max_points:
            del xs[:-self._max_points]
            del ys[:-self._max_points]

    # Trim helpers that keep X and all Y arrays in sync
    def _pg_trim_single_all(self) -> None:
        overflow = len(self._pg_x_single) - self._max_points
        if overflow > 0:
            del self._pg_x_single[:overflow]
            del self._pg_fx[:overflow]
            del self._pg_fy[:overflow]
            del self._pg_fz[:overflow]

    def _pg_trim_launch_all(self) -> None:
        overflow = len(self._pg_x_launch) - self._max_points
        if overflow > 0:
            del self._pg_x_launch[:overflow]
            del self._pg_lx[:overflow]
            del self._pg_ly[:overflow]
            del self._pg_lz[:overflow]

    def _pg_trim_land_all(self) -> None:
        overflow = len(self._pg_x_land) - self._max_points
        if overflow > 0:
            del self._pg_x_land[:overflow]
            del self._pg_rx[:overflow]
            del self._pg_ry[:overflow]
            del self._pg_rz[:overflow]

    def _pg_set_view_last_ms(self, window_ms: int = 10_000) -> None:
        # Clamp X range to show at most window_ms
        if self._plot_widget is None:
            return
        max_x = None
        if self._dual_enabled:
            if self._pg_x_launch:
                max_x = self._pg_x_launch[-1]
            if self._pg_x_land:
                mx = self._pg_x_land[-1]
                max_x = mx if max_x is None else max(max_x, mx)
        else:
            if self._pg_x_single:
                max_x = self._pg_x_single[-1]
        if max_x is None:
            return
        left = max(0, int(max_x) - int(window_ms))
        right = int(max_x)
        try:
            self._plot_widget.setXRange(left, right)  # type: ignore[attr-defined]
        except Exception:
            pass

    def _pg_update_y_range_min(self, min_abs: float = 10.0, headroom: float = 1.15) -> None:
        if self._plot_widget is None:
            return
        try:
            peaks: list[float] = []
            if not self._dual_enabled:
                for arr in (self._pg_fx, self._pg_fy, self._pg_fz):
                    if arr:
                        peaks.append(max(abs(v) for v in arr))
            else:
                if self._legend_launch.isChecked():
                    for arr in (self._pg_lx, self._pg_ly, self._pg_lz):
                        if arr:
                            peaks.append(max(abs(v) for v in arr))
                if self._legend_landing.isChecked():
                    for arr in (self._pg_rx, self._pg_ry, self._pg_rz):
                        if arr:
                            peaks.append(max(abs(v) for v in arr))
            peak = max(peaks) if peaks else 0.0
            target = max(min_abs, peak * headroom)
            self._plot_widget.setYRange(-target, target)  # type: ignore[attr-defined]
        except Exception:
            pass
    def _on_legend_toggle_change(self, _v: int) -> None:
        # Painter backend uses these in draw; pg backend must apply visibility
        if self._use_pg:
            self._apply_pg_series_visibility()
        self.update()

    def _apply_pg_series_visibility(self) -> None:
        if not self._use_pg:
            return
        # Respect dual state and individual toggles
        single_vis = not self._dual_enabled
        for key in ("fx", "fy", "fz"):
            try:
                self._pg_curves[key].setVisible(single_vis)  # type: ignore[union-attr]
            except Exception:
                pass
        launch_vis = self._dual_enabled and self._legend_launch.isChecked()
        for key in ("lx", "ly", "lz"):
            try:
                self._pg_curves[key].setVisible(launch_vis)  # type: ignore[union-attr]
            except Exception:
                pass
        land_vis = self._dual_enabled and self._legend_landing.isChecked()
        for key in ("rx", "ry", "rz"):
            try:
                self._pg_curves[key].setVisible(land_vis)  # type: ignore[union-attr]
            except Exception:
                pass
        # Update y-range with minimum height constraint after visibility change
        self._pg_update_y_range_min(10.0, 1.15)

    def set_temperature_f(self, value_f: Optional[float]) -> None:
        """Update the smoothed temperature label (°F) using a 10-second rolling average."""
        try:
            import time
            now = time.time()
            
            if value_f is not None:
                # Add sample
                self._temp_buffer.append((now, float(value_f)))
            
            # Prune samples older than 10 seconds
            cutoff = now - 10.0
            while self._temp_buffer and self._temp_buffer[0][0] < cutoff:
                self._temp_buffer.pop(0)
            
            # Update display at ~10Hz (every 0.1s)
            if now - self._last_temp_update_time >= 0.1:
                self._last_temp_update_time = now
                
                if not self._temp_buffer:
                    self._temp_label.setText("Temp: -- °F")
                else:
                    # Calculate average
                    total = sum(t for _, t in self._temp_buffer)
                    avg = total / len(self._temp_buffer)
                    self._temp_label.setText(f"Temp: {avg:.1f} °F")
                    
        except Exception:
            # Best-effort; never crash the UI on bad input
            pass

    def _recompute_autoscale(self) -> None:
        if self._use_pg:
            return
        if not self._auto_scale:
            return
        # Damping: skip recomputation until counter reaches N
        try:
            if self._autoscale_damp_enabled:
                self._autoscale_counter = (self._autoscale_counter + 1) % max(1, int(self._autoscale_every_n))
                if self._autoscale_counter != 0:
                    return
        except Exception:
            pass
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

            # Add a comfortable headroom; enforce a minimum of ±10
            target = max(peak * 1.15, 10.0)
            # Smooth changes to avoid flicker
            new_max = max(target, self._y_max * 0.8 + target * 0.2)
            self._y_max = new_max
            self._y_min = -new_max
        except Exception:
            pass

    def clear(self) -> None:
        if self._use_pg:
            self._pg_x_single.clear()
            self._pg_fx.clear()
            self._pg_fy.clear()
            self._pg_fz.clear()
            self._time0_ms = None
            self._pg_x_launch.clear()
            self._pg_lx.clear()
            self._pg_ly.clear()
            self._pg_lz.clear()
            self._pg_x_land.clear()
            self._pg_rx.clear()
            self._pg_ry.clear()
            self._pg_rz.clear()
            for k in ("fx", "fy", "fz", "lx", "ly", "lz", "rx", "ry", "rz"):
                try:
                    self._pg_curves[k].clear()  # type: ignore[union-attr]
                except Exception:
                    pass
        else:
            self._samples.clear()
            self._samples_launch.clear()
            self._samples_landing.clear()
            # Reset scale
            self._y_min = -10.0
            self._y_max = 10.0
            self._ema_fx = self._ema_fy = self._ema_fz = None
            self._ema_fx_launch = self._ema_fy_launch = self._ema_fz_launch = None
            self._ema_fx_landing = self._ema_fy_landing = self._ema_fz_landing = None
            self._last_raw_single = None
            self._last_smoothed_single = None
            self.update()

    def add_point(self, t_ms: int, fx: float, fy: float, fz: float) -> None:
        # Plot raw live data; overlay handles its own smoothing separately
        self._last_raw_single = (float(fx), float(fy), float(fz))
        if self._use_pg:
            if self._time0_ms is None:
                self._time0_ms = int(t_ms)
            self._pg_x_single.append(int(t_ms))
            self._pg_fx.append(float(fx))
            self._pg_fy.append(float(fy))
            self._pg_fz.append(float(fz))
            self._pg_trim_single_all()
            try:
                self._pg_curves["fx"].setData(self._pg_x_single, self._pg_fx)  # type: ignore[union-attr]
                self._pg_curves["fy"].setData(self._pg_x_single, self._pg_fy)  # type: ignore[union-attr]
                self._pg_curves["fz"].setData(self._pg_x_single, self._pg_fz)  # type: ignore[union-attr]
                self._pg_set_view_last_ms(10_000)
                self._pg_update_y_range_min(10.0, 1.15)
            except Exception:
                pass
        else:
            self._samples.append((t_ms, float(fx), float(fy), float(fz)))
            # Reset EMAs used previously for plotted series
            self._ema_fx = self._ema_fy = self._ema_fz = None
            if len(self._samples) > self._max_points:
                self._samples = self._samples[-self._max_points:]
            self._recompute_autoscale()
            self.update()
        self._update_overlay()

    # Dual-series API for mound mode
    def set_dual_series_enabled(self, enabled: bool) -> None:
        self._dual_enabled = bool(enabled)
        # Show/hide top-bar toggles in dual mode
        self._legend_container.setVisible(self._dual_enabled)
        if self._use_pg:
            self._apply_pg_series_visibility()
        else:
            self.update()

    def add_point_launch(self, t_ms: int, fx: float, fy: float, fz: float) -> None:
        if self._use_pg:
            if self._time0_ms is None:
                self._time0_ms = int(t_ms)
            self._pg_x_launch.append(int(t_ms))
            self._pg_lx.append(float(fx))
            self._pg_ly.append(float(fy))
            self._pg_lz.append(float(fz))
            self._pg_trim_launch_all()
            try:
                self._pg_curves["lx"].setData(self._pg_x_launch, self._pg_lx)  # type: ignore[union-attr]
                self._pg_curves["ly"].setData(self._pg_x_launch, self._pg_ly)  # type: ignore[union-attr]
                self._pg_curves["lz"].setData(self._pg_x_launch, self._pg_lz)  # type: ignore[union-attr]
                self._pg_set_view_last_ms(10_000)
                self._pg_update_y_range_min(10.0, 1.15)
            except Exception:
                pass
        else:
            # Plot raw in dual mode as well
            self._samples_launch.append((t_ms, float(fx), float(fy), float(fz)))
            self._ema_fx_launch = self._ema_fy_launch = self._ema_fz_launch = None
            if len(self._samples_launch) > self._max_points:
                self._samples_launch = self._samples_launch[-self._max_points:]
            self._recompute_autoscale()
            self.update()

    def add_point_landing(self, t_ms: int, fx: float, fy: float, fz: float) -> None:
        if self._use_pg:
            if self._time0_ms is None:
                self._time0_ms = int(t_ms)
            self._pg_x_land.append(int(t_ms))
            self._pg_rx.append(float(fx))
            self._pg_ry.append(float(fy))
            self._pg_rz.append(float(fz))
            self._pg_trim_land_all()
            try:
                self._pg_curves["rx"].setData(self._pg_x_land, self._pg_rx)  # type: ignore[union-attr]
                self._pg_curves["ry"].setData(self._pg_x_land, self._pg_ry)  # type: ignore[union-attr]
                self._pg_curves["rz"].setData(self._pg_x_land, self._pg_rz)  # type: ignore[union-attr]
                self._pg_set_view_last_ms(10_000)
                self._pg_update_y_range_min(10.0, 1.15)
            except Exception:
                pass
        else:
            # Plot raw in dual mode as well
            self._samples_landing.append((t_ms, float(fx), float(fy), float(fz)))
            self._ema_fx_landing = self._ema_fy_landing = self._ema_fz_landing = None
            if len(self._samples_landing) > self._max_points:
                self._samples_landing = self._samples_landing[-self._max_points:]
            self._recompute_autoscale()
            self.update()

    def set_autoscale_damping(self, enabled: bool, every_n: int) -> None:
        self._autoscale_damp_enabled = bool(enabled)
        try:
            self._autoscale_every_n = int(max(1, every_n))
        except Exception:
            self._autoscale_every_n = 1
        # Force immediate recompute next frame
        self._autoscale_counter = 0
        self.update()

    def _on_smooth_toggled(self, checked: bool) -> None:
        # Overlay removed; keep state but do nothing
        self._smoothing_enabled = bool(checked)
        self.update()

    def _update_overlay(self) -> None:
        # Overlay removed; no-op
        return

    def paintEvent(self, _e: QtGui.QPaintEvent) -> None:  # noqa: N802
        if self._use_pg and self._plot_widget is not None:
            # Only position the overlay inside the plot area; pyqtgraph draws the plot
            try:
                if self._value_container.isVisible():
                    self._value_container.adjustSize()
                    sz = self._value_container.sizeHint()
                    g = self._plot_widget.geometry()
                    cx = g.x() + 6
                    cy = g.y() + g.height() - sz.height() - 6
                    self._value_container.setGeometry(cx, cy, sz.width(), sz.height())
            except Exception:
                pass
            return
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
        # Legend toggles are placed in the top bar via layout
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
        p.end()
        # Position bottom-left overlay inside plot area
        try:
            if self._value_container.isVisible():
                self._value_container.adjustSize()
                sz = self._value_container.sizeHint()
                cx = x0 + 6
                cy = y0 + ph - sz.height() - 6
                self._value_container.setGeometry(cx, cy, sz.width(), sz.height())
        except Exception:
            pass


