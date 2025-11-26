from __future__ import annotations

from typing import Optional, Dict, List, Tuple
import os
import csv
import io

from PySide6 import QtCore, QtWidgets, QtGui

from ... import config
from .temp_slopes_widget import TempSlopesWidget


class TempPlotWidget(QtWidgets.QWidget):
    """
    Temperature-vs-force plot for discrete temperature testing.

    This is a focused port of the legacy Temp Plot tab from the old
    MainWindow, refactored into a standalone widget. It is responsible
    for:
      - Choosing phase (45 lb vs Bodyweight), sensor, and axis
      - Plotting data from discrete_temp_session.csv using pyqtgraph
      - Computing per-axis slopes and passing summary stats to a
        companion TempSlopesWidget
    """

    plot_metrics_updated = QtCore.Signal(object)  # metrics dict for TempSlopesWidget

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)

        self._csv_path: str = ""
        self._slopes_widget: Optional[TempSlopesWidget] = None
        self._temp_slope_avgs: Dict[str, Dict[str, float]] = {}
        self._temp_slope_stds: Dict[str, Dict[str, float]] = {}
        self._temp_weight_models: Dict[str, Dict[str, float]] = {}

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # Try to use pyqtgraph backend
        self._pg = None  # type: ignore[assignment]
        self._plot_widget: Optional[QtWidgets.QWidget] = None
        try:
            import pyqtgraph as pg  # type: ignore[import-not-found]

            self._pg = pg
            self._plot_widget = pg.PlotWidget(
                background=tuple(getattr(config, "COLOR_BG", (18, 18, 20)))
            )
            try:
                self._plot_widget.showGrid(x=True, y=True, alpha=0.3)  # type: ignore[attr-defined]
                self._plot_widget.setLabel("bottom", "Temperature (°F)")  # type: ignore[attr-defined]
                self._plot_widget.setLabel("left", "Force")  # type: ignore[attr-defined]
            except Exception:
                pass
            root.addWidget(self._plot_widget, 1)
        except Exception:
            # Fallback: simple label if pyqtgraph not available
            self._pg = None
            self._plot_widget = None
            lbl = QtWidgets.QLabel("Temperature plot requires pyqtgraph; plot output not available.")
            lbl.setStyleSheet("color: rgb(220,220,230);")
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            root.addWidget(lbl, 1)

        # Controls row for phase / sensor / axis selection
        ctrl_row = QtWidgets.QHBoxLayout()
        ctrl_row.setContentsMargins(0, 0, 0, 0)
        ctrl_row.setSpacing(8)
        ctrl_row.addWidget(QtWidgets.QLabel("Phase:"))
        self.phase_combo = QtWidgets.QComboBox()
        self.phase_combo.addItems(["Bodyweight", "45 lb"])
        ctrl_row.addWidget(self.phase_combo)
        ctrl_row.addWidget(QtWidgets.QLabel("Sensor:"))
        self.sensor_combo = QtWidgets.QComboBox()
        self.sensor_combo.addItems(
            [
                "Sum",
                "Rear Right Outer",
                "Rear Right Inner",
                "Rear Left Outer",
                "Rear Left Inner",
                "Front Left Outer",
                "Front Left Inner",
                "Front Right Outer",
                "Front Right Inner",
            ]
        )
        ctrl_row.addWidget(self.sensor_combo)
        ctrl_row.addWidget(QtWidgets.QLabel("Axis:"))
        self.axis_combo = QtWidgets.QComboBox()
        self.axis_combo.addItems(["z", "x", "y"])
        ctrl_row.addWidget(self.axis_combo)
        ctrl_row.addStretch(1)
        root.addLayout(ctrl_row)

        # Re-plot when any Temp Plot setting changes (if a test is selected)
        try:
            self.phase_combo.currentIndexChanged.connect(lambda _i: self.plot_current())
            self.sensor_combo.currentIndexChanged.connect(lambda _i: self.plot_current())
            self.axis_combo.currentIndexChanged.connect(lambda _i: self.plot_current())
        except Exception:
            pass

    # --- Public API ---------------------------------------------------------

    def set_slopes_widget(self, widget: TempSlopesWidget) -> None:
        """Attach a companion TempSlopesWidget to receive slope/plot metrics."""
        self._slopes_widget = widget
        try:
            self.plot_metrics_updated.connect(widget.set_current_plot_stats)
        except Exception:
            pass

    @QtCore.Slot(str)
    def set_test_path(self, path: str) -> None:
        """Set the active discrete_temp_session.csv file (folder or file path)."""
        # Caller will typically pass the folder; we normalize to CSV path here.
        p = str(path or "").strip()
        if not p:
            self._csv_path = ""
            return
        if os.path.isdir(p):
            candidate = os.path.join(p, "discrete_temp_session.csv")
            self._csv_path = candidate if os.path.isfile(candidate) else ""
        else:
            self._csv_path = p if os.path.isfile(p) else ""

    @QtCore.Slot()
    def plot_current(self) -> None:
        """Plot temperature vs force for the currently selected discrete test."""
        if not self._csv_path or self._plot_widget is None or self._pg is None:
            return

        # Compute/update slopes first so TempSlopesWidget stays in sync
        avgs, stds, weight_models = self._compute_discrete_temp_slopes(self._csv_path)
        self._temp_slope_avgs = avgs
        self._temp_slope_stds = stds
        self._temp_weight_models = weight_models
        if self._slopes_widget is not None:
            try:
                self._slopes_widget.set_slopes(avgs, stds)
            except Exception:
                pass

        # Then build the plot using the same logic as the legacy _on_plot_discrete_test
        self._plot_from_models(self._csv_path)

    # --- Internal helpers ---------------------------------------------------

    def _compute_discrete_temp_slopes(
        self, csv_path: str
    ) -> Tuple[Dict[str, Dict[str, float]], Dict[str, Dict[str, float]], Dict[str, Dict[str, float]]]:
        """
        Port of the legacy _compute_discrete_temp_slopes from old_main_window.

        Returns:
          (avgs, stds, weight_models)
        """
        phases = ("45lb", "bodyweight")
        axes = ("x", "y", "z")
        sensor_prefixes = [
            "rear-right-outer",
            "rear-right-inner",
            "rear-left-outer",
            "rear-left-inner",
            "front-left-outer",
            "front-left-inner",
            "front-right-outer",
            "front-right-inner",
        ]
        data: Dict[str, Dict[str, Dict[str, List[Tuple[float, float]]]]] = {
            ph: {ax: {sp: [] for sp in sensor_prefixes} for ax in axes} for ph in phases
        }
        phase_loads: Dict[str, List[float]] = {ph: [] for ph in phases}

        try:
            with open(csv_path, "r", encoding="utf-8", newline="") as f:
                header_line = f.readline()
                if not header_line:
                    return {}, {}, {}
                header_reader = csv.reader(io.StringIO(header_line))
                headers = next(header_reader, [])
                headers = [h.strip() for h in headers]
                reader = csv.DictReader(f, fieldnames=headers, skipinitialspace=True)

                for row in reader:
                    if not row:
                        continue
                    clean_row = {k.strip(): v for k, v in row.items() if k}
                    try:
                        phase_raw = str(
                            clean_row.get("phase_name") or clean_row.get("phase") or ""
                        ).strip().lower()
                    except Exception:
                        continue
                    if phase_raw not in phases:
                        continue
                    phase = phase_raw
                    try:
                        temp_f = float(clean_row.get("sum-t") or 0.0)
                    except Exception:
                        continue
                    # Average per-sensor load from sum-z
                    try:
                        sum_z = float(clean_row.get("sum-z") or 0.0)
                        if sum_z != 0.0:
                            phase_loads[phase].append(abs(sum_z) / 8.0)
                    except Exception:
                        pass
                    for sp in sensor_prefixes:
                        for ax in axes:
                            col = f"{sp}-{ax}"
                            try:
                                val = float(clean_row.get(col) or 0.0)
                            except Exception:
                                continue
                            data[phase][ax][sp].append((temp_f, val))
        except Exception:
            return {}, {}, {}

        slopes_by_sensor: Dict[str, Dict[str, Dict[str, float]]] = {
            ph: {ax: {} for ax in axes} for ph in phases
        }
        slope_lists_phase: Dict[str, Dict[str, List[float]]] = {
            ph: {ax: [] for ax in axes} for ph in phases
        }
        slope_lists_all: Dict[str, List[float]] = {ax: [] for ax in axes}

        def _fit_slope(points: List[Tuple[float, float]]) -> float:
            if len(points) < 2:
                return 0.0
            baseline_low = 74.0
            baseline_high = 78.0
            target_T = 76.0
            baseline = [(t, y) for (t, y) in points if baseline_low <= t <= baseline_high]
            if baseline:
                try:
                    weights: List[float] = []
                    for t, _y in baseline:
                        try:
                            dt = abs(float(t) - target_T)
                        except Exception:
                            dt = 0.0
                        w = 1.0 / (1.0 + dt)
                        weights.append(w)
                    w_sum = sum(weights)
                    if w_sum <= 0.0:
                        raise ValueError("baseline_weight_sum_zero")
                    T0 = sum(w * t for (w, (t, _)) in zip(weights, baseline)) / w_sum
                    Y0 = sum(w * y for (w, (_, y)) in zip(weights, baseline)) / w_sum
                except Exception:
                    try:
                        T0 = sum(t for t, _ in baseline) / float(len(baseline))
                        Y0 = sum(y for _, y in baseline) / float(len(baseline))
                    except Exception:
                        T0, Y0 = baseline[0]
                num = 0.0
                den = 0.0
                for (t, y) in points:
                    dt = t - T0
                    dy = y - Y0
                    num += dt * dy
                    den += dt * dt
                if den <= 0.0:
                    return 0.0
                return num / den
            # Ordinary least squares
            try:
                mean_t = sum(t for t, _ in points) / float(len(points))
                mean_y = sum(y for _, y in points) / float(len(points))
            except Exception:
                return 0.0
            num = 0.0
            den = 0.0
            for (t, y) in points:
                dt = t - mean_t
                dy = y - mean_y
                num += dt * dy
                den += dt * dt
            if den <= 0.0:
                return 0.0
            return num / den

        for ph in phases:
            for ax in axes:
                for sp, pts in data.get(ph, {}).get(ax, {}).items():
                    if not pts or len(pts) < 2:
                        continue
                    try:
                        m = _fit_slope(pts)
                    except Exception:
                        m = 0.0
                    slopes_by_sensor[ph][ax][sp] = m
                    slope_lists_phase[ph][ax].append(m)
                    slope_lists_all[ax].append(m)

        avgs: Dict[str, Dict[str, float]] = {"bodyweight": {}, "45lb": {}, "all": {}}
        stds: Dict[str, Dict[str, float]] = {"bodyweight": {}, "45lb": {}, "all": {}}
        weight_models: Dict[str, Dict[str, float]] = {}

        for ax in axes:
            for ph in phases:
                vals = slope_lists_phase[ph][ax]
                if vals:
                    mu = sum(vals) / float(len(vals))
                    var = sum((v - mu) ** 2 for v in vals) / float(len(vals))
                    avgs[ph][ax] = mu
                    stds[ph][ax] = var ** 0.5
                else:
                    avgs[ph][ax] = 0.0
                    stds[ph][ax] = 0.0
            vals_all = slope_lists_all[ax]
            if vals_all:
                mu_all = sum(vals_all) / float(len(vals_all))
                var_all = sum((v - mu_all) ** 2 for v in vals_all) / float(len(vals_all))
                avgs["all"][ax] = mu_all
                stds["all"][ax] = var_all ** 0.5
            else:
                avgs["all"][ax] = 0.0
                stds["all"][ax] = 0.0

            # Build simple linear "multiplier vs load" model for this axis
            try:
                base = float(avgs.get("all", {}).get(ax, 0.0))
            except Exception:
                base = 0.0
            try:
                s45 = float(avgs.get("45lb", {}).get(ax, 0.0))
            except Exception:
                s45 = 0.0
            try:
                sBW = float(avgs.get("bodyweight", {}).get(ax, 0.0))
            except Exception:
                sBW = 0.0
            loads_45 = phase_loads.get("45lb") or []
            loads_bw = phase_loads.get("bodyweight") or []
            F45 = sum(loads_45) / float(len(loads_45)) if loads_45 else 0.0
            FBW = sum(loads_bw) / float(len(loads_bw)) if loads_bw else 0.0
            if base != 0.0:
                k45 = s45 / base
                kBW = sBW / base
            else:
                k45 = 1.0
                kBW = 1.0
            if F45 > 0.0 and FBW > F45:
                b = (kBW - k45) / (FBW - F45)
                a = k45 - b * F45
            else:
                a = 1.0
                b = 0.0
            weight_models[ax] = {
                "base": base,
                "k45": k45,
                "kBW": kBW,
                "F45": F45,
                "FBW": FBW,
                "a": a,
                "b": b,
            }

        return avgs, stds, weight_models

    def _plot_from_models(self, csv_path: str) -> None:
        """
        Port of the legacy _on_plot_discrete_test, using the precomputed
        avgs/weight_models stored on this widget to build the actual plot
        and "Current Plot" metrics.
        """
        assert self._plot_widget is not None and self._pg is not None

        if not os.path.isfile(csv_path) or os.path.getsize(csv_path) <= 0:
            return

        phase_label = str(self.phase_combo.currentText() or "Bodyweight").strip().lower()
        if phase_label.startswith("45"):
            phase_name = "45lb"
        else:
            phase_name = "bodyweight"
        sensor_label = str(self.sensor_combo.currentText() or "Sum").strip()
        axis_label = str(self.axis_combo.currentText() or "z").strip().lower()
        if axis_label not in ("x", "y", "z"):
            axis_label = "z"

        name_map = {
            "Sum": "sum",
            "Rear Right Outer": "rear-right-outer",
            "Rear Right Inner": "rear-right-inner",
            "Rear Left Outer": "rear-left-outer",
            "Rear Left Inner": "rear-left-inner",
            "Front Left Outer": "front-left-outer",
            "Front Left Inner": "front-left-inner",
            "Front Right Outer": "front-right-outer",
            "Front Right Inner": "front-right-inner",
        }
        prefix = name_map.get(sensor_label, "sum")
        col_name = f"{prefix}-{axis_label}"

        xs: List[float] = []
        ys: List[float] = []
        loads_per_sensor: List[float] = []
        baseline_pts_for_plot: List[Tuple[float, float]] = []
        baseline_low = 74.0
        baseline_high = 78.0
        target_T = 76.0

        try:
            with open(csv_path, "r", encoding="utf-8", newline="") as f:
                header_line = f.readline()
                if not header_line:
                    return
                header_reader = csv.reader(io.StringIO(header_line))
                headers = next(header_reader, [])
                headers = [h.strip() for h in headers]
                reader = csv.DictReader(f, fieldnames=headers, skipinitialspace=True)

                row_count = 0
                match_count = 0
                for row in reader:
                    if not row:
                        continue
                    row_count += 1
                    ph = str(row.get("phase_name") or row.get("phase") or "").strip().lower()
                    try:
                        temp_f = float(row.get("sum-t") or 0.0)
                        y_val = float(row.get(col_name) or 0.0)
                    except Exception:
                        continue
                    if ph != phase_name:
                        continue
                    match_count += 1
                    try:
                        if baseline_low <= float(temp_f) <= baseline_high:
                            baseline_pts_for_plot.append((float(temp_f), float(y_val)))
                    except Exception:
                        pass
                    xs.append(temp_f)
                    ys.append(y_val)
                    try:
                        if sensor_label.lower().startswith("sum"):
                            sum_z = float(row.get("sum-z") or 0.0)
                            if sum_z != 0.0:
                                loads_per_sensor.append(abs(sum_z) / 8.0)
                        else:
                            z_col = f"{prefix}-z"
                            fz_sensor = float(row.get(z_col) or 0.0)
                            if fz_sensor != 0.0:
                                loads_per_sensor.append(abs(fz_sensor))
                    except Exception:
                        pass
        except Exception:
            xs, ys = [], []

        if not xs or not ys or len(xs) != len(ys):
            return

        # Collapse baseline measurements into a single weighted point for regression
        try:
            pts_all = list(zip(xs, ys))
            baseline_idx: List[int] = [
                i for i, (t, _y) in enumerate(pts_all) if baseline_low <= float(t) <= baseline_high
            ]
            if len(baseline_idx) > 1:
                weights: List[float] = []
                baseline_vals: List[Tuple[float, float]] = []
                for i in baseline_idx:
                    t, yv = pts_all[i]
                    try:
                        dt = abs(float(t) - target_T)
                    except Exception:
                        dt = 0.0
                    w = 1.0 / (1.0 + dt)
                    weights.append(w)
                    baseline_vals.append((float(t), float(yv)))
                w_sum = sum(weights)
                if w_sum > 0.0 and baseline_vals:
                    T0 = sum(w * t for w, (t, _y) in zip(weights, baseline_vals)) / w_sum
                    Y0 = sum(w * y for w, (_t, y) in zip(weights, baseline_vals)) / w_sum
                    keep_pts = [p for i, p in enumerate(pts_all) if i not in baseline_idx]
                    keep_pts.append((T0, Y0))
                    xs = [p[0] for p in keep_pts]
                    ys = [p[1] for p in keep_pts]
        except Exception:
            pass

        pts = sorted(zip(xs, ys), key=lambda p: p[0])
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]

        # Use precomputed all-tests slope for this axis; scale for Sum plots
        avgs = self._temp_slope_avgs or {}
        try:
            m_all = float(avgs.get("all", {}).get(axis_label, 0.0))
        except Exception:
            m_all = 0.0
        m_solid = m_all
        if sensor_label.lower().startswith("sum"):
            m_solid = m_all * 8.0

        # Weight-adjusted slope using simple linear model
        models = self._temp_weight_models or {}
        m_model = models.get(axis_label, {}) or {}
        try:
            base = float(m_model.get("base", m_all))
        except Exception:
            base = m_all
        try:
            a = float(m_model.get("a", 1.0))
        except Exception:
            a = 1.0
        try:
            b = float(m_model.get("b", 0.0))
        except Exception:
            b = 0.0
        try:
            F45 = float(m_model.get("F45", 0.0))
            FBW = float(m_model.get("FBW", 0.0))
        except Exception:
            F45, FBW = 0.0, 0.0

        F_ref = sum(loads_per_sensor) / float(len(loads_per_sensor)) if loads_per_sensor else 0.0
        if F_ref > 0.0:
            try:
                k_ref = a + b * F_ref
            except Exception:
                k_ref = a
        else:
            k_ref = a
        m_eff_single = base * k_ref
        if sensor_label.lower().startswith("sum"):
            m_dashed = m_eff_single * 8.0
        else:
            m_dashed = m_eff_single

        # Compute intercepts
        if len(xs) >= 1:
            try:
                mean_t = sum(xs) / float(len(xs))
                mean_y = sum(ys) / float(len(ys))
                b_solid = mean_y - m_solid * mean_t
                b_dashed = mean_y - m_dashed * mean_t
            except Exception:
                b_solid = ys[0]
                b_dashed = ys[0]
        else:
            b_solid = 0.0
            b_dashed = 0.0

        try:
            self._plot_widget.clear()  # type: ignore[union-attr]
            try:
                axis_label_full = axis_label.upper()
                self._plot_widget.setLabel("bottom", "Temperature (°F)")  # type: ignore[attr-defined]
                self._plot_widget.setLabel(
                    "left", f"{sensor_label} {axis_label_full}"
                )  # type: ignore[attr-defined]
            except Exception:
                pass

            try:
                solid_ys = [b_solid + m_solid * t for t in xs]
            except Exception:
                solid_ys = ys
            try:
                dashed_ys = [b_dashed + m_dashed * t for t in xs]
            except Exception:
                dashed_ys = ys

            base_color = (180, 180, 255)
            solid_pen = self._pg.mkPen(color=base_color, width=2)  # type: ignore[attr-defined]
            dashed_pen = self._pg.mkPen(
                color=base_color, width=2, style=QtCore.Qt.DashLine
            )  # type: ignore[attr-defined]

            # Global all-tests line (solid)
            self._plot_widget.plot(xs, solid_ys, pen=solid_pen)  # type: ignore[attr-defined]
            # Load-adjusted line (dashed)
            self._plot_widget.plot(xs, dashed_ys, pen=dashed_pen)  # type: ignore[attr-defined]

            # Baseline points
            try:
                if baseline_pts_for_plot:
                    bx = [float(p[0]) for p in baseline_pts_for_plot]
                    by = [float(p[1]) for p in baseline_pts_for_plot]
                    self._plot_widget.plot(  # type: ignore[attr-defined]
                        bx,
                        by,
                        pen=None,
                        symbol="o",
                        symbolSize=6,
                        symbolBrush=self._pg.mkBrush(180, 180, 255, 90),
                        symbolPen=self._pg.mkPen(color=(180, 180, 255, 60), width=1),
                    )
            except Exception:
                pass

            # Data points + connecting line
            self._plot_widget.plot(
                xs,
                ys,
                pen=self._pg.mkPen(color=(120, 220, 120), width=1),  # type: ignore[attr-defined]
                symbol="o",
                symbolBrush=(200, 250, 200),
                symbolSize=8,
            )

            # SSE-based improvement metric
            try:
                sse_solid = sum((float(y) - float(yb)) ** 2 for y, yb in zip(ys, solid_ys))
                sse_dashed = sum((float(y) - float(yd)) ** 2 for y, yd in zip(ys, dashed_ys))
                if sse_solid > 0.0:
                    improve_pct = (sse_solid - sse_dashed) / sse_solid * 100.0
                else:
                    improve_pct = 0.0
            except Exception:
                improve_pct = 0.0

            # Push current-plot stats to TempSlopesWidget
            metrics = {
                "base": float(m_solid),
                "mult": float(k_ref),
                "adj": float(m_dashed),
                "improve_pct": float(improve_pct),
                "a": float(a),
                "b": float(b),
                "Fref": float(F_ref),
                "is_sum": bool(sensor_label.lower().startswith("sum")),
            }
            self.plot_metrics_updated.emit(metrics)
        except Exception:
            pass


