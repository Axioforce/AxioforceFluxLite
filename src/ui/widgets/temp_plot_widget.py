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
        self._temp_slope_coeffs_by_sensor: Dict[str, Dict[str, Dict[str, float]]] = {}

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
            # Re-plot when toggles change
            widget.toggles_changed.connect(self.plot_current)
        except Exception:
            pass

    @QtCore.Slot(str)
    def set_test_path(self, path: str) -> None:
        """Set the active discrete_temp_session.csv file (folder or file path)."""
        # Caller will typically pass the folder; we normalize to CSV path here.
        p = str(path or "").strip()

        def _clear():
            self._csv_path = ""
            if self._plot_widget is not None:
                try:
                    self._plot_widget.clear()
                except Exception:
                    pass
            if self._slopes_widget is not None:
                try:
                    self._slopes_widget.set_slopes({}, {})
                    self._slopes_widget.set_current_plot_stats({})
                except Exception:
                    pass

        if not p:
            _clear()
            return

        if os.path.isdir(p):
            candidate = os.path.join(p, "discrete_temp_session.csv")
            self._csv_path = candidate if os.path.isfile(candidate) else ""
        else:
            self._csv_path = p if os.path.isfile(p) else ""

        if not self._csv_path:
            _clear()
        else:
            # Auto-plot when a valid test is selected
            self.plot_current()

    @QtCore.Slot()
    def plot_current(self) -> None:
        """Plot temperature vs force for the currently selected discrete test."""
        if not self._csv_path or self._plot_widget is None or self._pg is None:
            return

        # Compute/update slopes first so TempSlopesWidget stays in sync
        avgs, stds, weight_models, coeffs, coeffs_by_sensor = self._compute_discrete_temp_slopes(
            self._csv_path
        )
        self._temp_slope_avgs = avgs
        self._temp_slope_stds = stds
        self._temp_weight_models = weight_models
        self._temp_slope_coeffs = coeffs  # Cache coeffs on self for plot logic to access
        self._temp_slope_coeffs_by_sensor = coeffs_by_sensor
        
        if self._slopes_widget is not None:
            try:
                self._slopes_widget.set_slopes(avgs, stds, coeffs)
            except Exception:
                pass

        # Then build the plot using the same logic as the legacy _on_plot_discrete_test
        self._plot_from_models(self._csv_path)

    # --- Internal helpers ---------------------------------------------------

    def _compute_discrete_temp_slopes(
        self, csv_path: str
    ) -> Tuple[
        Dict[str, Dict[str, float]],
        Dict[str, Dict[str, float]],
        Dict[str, Dict[str, float]],
        Dict[str, Dict[str, float]],
        Dict[str, Dict[str, Dict[str, float]]],
    ]:
        """
        Port of the legacy _compute_discrete_temp_slopes from old_main_window.

        Returns:
          (avgs, stds, weight_models, coeffs, coeffs_by_sensor)
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
                    return {}, {}, {}, {}
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
            return {}, {}, {}, {}

        slopes_by_sensor: Dict[str, Dict[str, Dict[str, float]]] = {
            ph: {ax: {} for ax in axes} for ph in phases
        }
        slope_lists_phase: Dict[str, Dict[str, List[float]]] = {
            ph: {ax: [] for ax in axes} for ph in phases
        }
        slope_lists_all: Dict[str, List[float]] = {ax: [] for ax in axes}

        # Coeff containers
        coef_lists_phase: Dict[str, Dict[str, List[float]]] = {
            ph: {ax: [] for ax in axes} for ph in phases
        }
        coef_lists_all: Dict[str, List[float]] = {ax: [] for ax in axes}

        def _get_baseline(points: List[Tuple[float, float]]) -> Tuple[float, float]:
            baseline_low = 74.0
            baseline_high = 78.0
            target_T = 76.0
            baseline = [
                (t, y) for (t, y) in points if baseline_low <= t <= baseline_high
            ]
            if not baseline:
                # Fallback: take average of all points if no baseline found (unlikely but safe)
                if not points:
                    return 0.0, 0.0
                # Try to use points closest to target_T?
                # For now just return 0,0 which signals invalid baseline
                return 0.0, 0.0

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
                return T0, Y0
            except Exception:
                try:
                    T0 = sum(t for t, _ in baseline) / float(len(baseline))
                    Y0 = sum(y for _, y in baseline) / float(len(baseline))
                    return T0, Y0
                except Exception:
                    return baseline[0]

        def _fit_slope(points: List[Tuple[float, float]]) -> float:
            if len(points) < 2:
                return 0.0
            T0, Y0 = _get_baseline(points)
            if T0 == 0.0 and Y0 == 0.0:
                # Fallback to simple regression if baseline detection failed
                pass 
            
            # Using T0, Y0 as anchor
            num = 0.0
            den = 0.0
            for t, y in points:
                dt = t - T0
                dy = y - Y0
                num += dt * dy
                den += dt * dt
            if den <= 0.0:
                return 0.0
            return num / den

        def _compute_coef(points: List[Tuple[float, float]]) -> float:
            if len(points) < 2:
                return 0.0
            T0, Y0 = _get_baseline(points)
            if abs(Y0) < 1e-6:
                return 0.0
            
            # --- NEW APPROACH: Regression-based Coefficient ---
            # Calculate slope (m) of the entire dataset and normalize by baseline (Y0).
            # This avoids noise amplification at small dt that occurs with point-by-point averaging.
            m = _fit_slope(points)
            return m / Y0

            # --- OLD METHOD (Commented Out) ---
            # (1 - (y / Y0)) / (T0 - t)
            # Average this over points for this sensor
            # NOTE: We use T0 - t because we expect y to decrease as t decreases if coef is positive (sensitivity?)
            # Wait, if temp drops (T0 > t), and value drops (y < Y0), then (1 - y/Y0) is positive.
            # (T0 - t) is positive. So coef is positive.
            # If value increases as temp drops, (1 - y/Y0) is negative. Coef is negative.
            # cs = []
            # for t, y in points:
            #     dt = T0 - t
            #     # Avoid points too close to baseline temp to prevent noise amplification
            #     if abs(dt) < 0.5:
            #         continue
            #     try:
            #         # Coef calculation as specified:
            #         # percent_off = 1 - (y / Y0)
            #         # per_degree = percent_off / dt
            #         c = (1.0 - (y / Y0)) / dt
            #         cs.append(c)
            #         
            #         # DEBUG: Print sample calc for one sensor to verify
            #         if abs(dt) > 30.0 and len(cs) == 1:
            #              print(f"[DEBUG COEF] T0={T0:.2f}, Y0={Y0:.2f}, t={t:.2f}, y={y:.2f}, dt={dt:.2f}, pct_off={(1-y/Y0):.4f}, c={c:.6f}")
            #     except Exception:
            #         pass
            # if not cs:
            #     return 0.0
            # 
            # # Average across all valid temperature points for this sensor
            # return sum(cs) / float(len(cs))

        # First pass: Calculate per-sensor coefficients
        # slopes_by_sensor structure is already: [phase][axis][sensor] -> float
        # We need a similar structure for coeffs
        
        # Reset containers to ensure clean state
        coef_lists_phase = {ph: {ax: [] for ax in axes} for ph in phases}
        coef_lists_all = {ax: [] for ax in axes}
        
        # New: Store full breakdown of coeffs for plotting specific sensors
        # Structure: coeffs_by_sensor[phase][axis][sensor_name] -> float
        coeffs_by_sensor: Dict[str, Dict[str, Dict[str, float]]] = {
            ph: {ax: {} for ax in axes} for ph in phases
        }
        
        # --- NEW LOGIC: Calculate Global Z-Axis Coef from Sum-Z ---
        # We want to derive the Z-coef from the total force (sum-z) drift, 
        # as it is more robust than averaging noisy individual sensors.
        
        # 1. Build Sum-Z points for each phase
        sum_z_data: Dict[str, List[Tuple[float, float]]] = {ph: [] for ph in phases}
        
        # Re-read file to get sum-z vs sum-t directly? 
        # Actually we can just iterate the rows we already parsed? No, we parsed into 'data' dict.
        # We need to re-parse or store 'sum' in 'data'. 
        # The 'data' dict structure is data[phase][axis][sensor]. 
        # We don't have 'sum' as a sensor there.
        
        # Let's do a quick re-parse for sum-z specifically or modify the initial parse loop.
        # Modifying initial parse loop is cleaner but let's just do a quick pass here to avoid touching that massive block.
        # Actually, let's look at the initial parse block...
        
        # We will iterate the file again? No, that's inefficient.
        # Let's add 'sum' to the sensor_prefixes list temporarily? No, 'sum-z' vs 'rear-right-outer-z'.
        
        # Let's just create a helper to extract sum-z points from the file quickly.
        try:
            with open(csv_path, "r", encoding="utf-8", newline="") as f:
                header_line = f.readline()
                if header_line:
                    header_reader = csv.reader(io.StringIO(header_line))
                    headers = next(header_reader, [])
                    headers = [h.strip() for h in headers]
                    reader = csv.DictReader(f, fieldnames=headers, skipinitialspace=True)
                    for row in reader:
                        if not row: continue
                        clean_row = {k.strip(): v for k, v in row.items() if k}
                        ph = str(clean_row.get("phase_name") or clean_row.get("phase") or "").strip().lower()
                        if ph not in phases: continue
                        try:
                            t_val = float(clean_row.get("sum-t") or 0.0)
                            z_val = float(clean_row.get("sum-z") or 0.0)
                            sum_z_data[ph].append((t_val, z_val))
                        except:
                            pass
        except Exception:
            pass

        # 2. Calculate Coef from Sum-Z for each phase
        global_z_coefs: Dict[str, float] = {}
        for ph in phases:
            pts = sum_z_data.get(ph, [])
            if not pts:
                global_z_coefs[ph] = 0.0
                continue
            try:
                # We need to compute coef for sum-z
                # Note: sum-z is usually negative (downward force). 
                # Formula: (1 - y/Y0)/dt. 
                # If Y0 is -1000, and y is -900 (less force), y/Y0 = 0.9. 1-0.9 = 0.1.
                # If temp dropped (dt > 0), coef is positive.
                # If force became "more negative" (e.g. -1100), y/Y0 = 1.1. 1-1.1 = -0.1. Coef negative.
                c = _compute_coef(pts)
                global_z_coefs[ph] = c
            except Exception:
                global_z_coefs[ph] = 0.0

        # Calculate "All" average for Z
        z_vals_all = [global_z_coefs[p] for p in phases if p in global_z_coefs]
        global_z_all = sum(z_vals_all) / len(z_vals_all) if z_vals_all else 0.0
        
        # -----------------------------------------------------------

        for ph in phases:
            for ax in axes:
                for sp, pts in data.get(ph, {}).get(ax, {}).items():
                    if not pts or len(pts) < 2:
                        continue
                        
                    # Calculate Slope (existing logic)
                    try:
                        m = _fit_slope(pts)
                    except Exception:
                        m = 0.0
                    slopes_by_sensor[ph][ax][sp] = m
                    slope_lists_phase[ph][ax].append(m)
                    slope_lists_all[ax].append(m)
                    
                    # Calculate Coef
                    if ax == "z":
                        # FORCE OVERRIDE: Use the Global Sum-Z Coef for all Z-axis sensors
                        c = global_z_coefs.get(ph, 0.0)
                    else:
                        # For X/Y, stick to per-sensor calculation
                        try:
                            c = _compute_coef(pts)
                        except Exception:
                            c = 0.0
                    
                    # Store per-sensor coefficient
                    coeffs_by_sensor[ph][ax][sp] = c
                    
                    # Store per-sensor coefficient in lists for averaging later
                    # For Z, this will just append the same global value 8 times, which is fine
                    coef_lists_phase[ph][ax].append(c)
                    coef_lists_all[ax].append(c)

        avgs: Dict[str, Dict[str, float]] = {"bodyweight": {}, "45lb": {}, "all": {}}
        stds: Dict[str, Dict[str, float]] = {"bodyweight": {}, "45lb": {}, "all": {}}
        coeffs: Dict[str, Dict[str, float]] = {"bodyweight": {}, "45lb": {}, "all": {}}
        weight_models: Dict[str, Dict[str, float]] = {}

        # Second pass: Average per-sensor coefficients to get per-axis values
        for ax in axes:
            for ph in phases:
                # Slopes processing
                vals = slope_lists_phase[ph][ax]
                if vals:
                    mu = sum(vals) / float(len(vals))
                    var = sum((v - mu) ** 2 for v in vals) / float(len(vals))
                    avgs[ph][ax] = mu
                    stds[ph][ax] = var ** 0.5
                else:
                    avgs[ph][ax] = 0.0
                    stds[ph][ax] = 0.0
                
                # Coeffs processing: Average of per-sensor averages
                c_vals = coef_lists_phase[ph][ax]
                if c_vals:
                    c_mu = sum(c_vals) / float(len(c_vals))
                    coeffs[ph][ax] = c_mu
                else:
                    coeffs[ph][ax] = 0.0

            # All-phases processing
            vals_all = slope_lists_all[ax]
            if vals_all:
                mu_all = sum(vals_all) / float(len(vals_all))
                var_all = sum((v - mu_all) ** 2 for v in vals_all) / float(len(vals_all))
                avgs["all"][ax] = mu_all
                stds["all"][ax] = var_all ** 0.5
            else:
                avgs["all"][ax] = 0.0
                stds["all"][ax] = 0.0
            
            c_vals_all = coef_lists_all[ax]
            if c_vals_all:
                c_mu_all = sum(c_vals_all) / float(len(c_vals_all))
                coeffs["all"][ax] = c_mu_all
            else:
                coeffs["all"][ax] = 0.0
            
            if ax == "z":
                # For "All" phase, average the phase globals
                z_vals_all = [global_z_coefs.get(p, 0.0) for p in phases]
                if z_vals_all:
                     coeffs["all"][ax] = sum(z_vals_all) / float(len(z_vals_all))

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

        return avgs, stds, weight_models, coeffs, coeffs_by_sensor

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

        # Coef
        coeffs = self._temp_slope_coeffs or {} if hasattr(self, "_temp_slope_coeffs") else {}
        coeffs_by_sensor = self._temp_slope_coeffs_by_sensor or {} if hasattr(self, "_temp_slope_coeffs_by_sensor") else {}
        
        try:
            # We must fetch the coefficient for the CURRENT phase (bodyweight or 45lb), not "all"
            # The line plotting logic needs to match the user's current selection.
            # phase_label is e.g. "bodyweight" or "45 lb" -> mapped to "bodyweight" / "45lb"
            c_phase_key = "bodyweight" if "body" in phase_label.lower() else "45lb"
            
            # Logic: If looking at a specific sensor, use THAT sensor's coef.
            # If looking at "Sum", use the global average for the axis.
            if sensor_label.lower().startswith("sum"):
                c_val = float(coeffs.get(c_phase_key, {}).get(axis_label, 0.0))
            else:
                # Need to map UI sensor name (e.g. "Rear Right Outer") to key (e.g. "rear-right-outer")
                sensor_key = name_map.get(sensor_label, "")
                if not sensor_key:
                    sensor_key = sensor_label.lower().replace(" ", "-")
                c_val = float(coeffs_by_sensor.get(c_phase_key, {}).get(axis_label, {}).get(sensor_key, 0.0))
                
        except Exception:
            c_val = 0.0
        c_line_slope = c_val # This is actually just the coef C
        
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

        # Compute intercepts and plot logic
        # Retrieve toggles
        toggles = {"show_base": True, "show_adj": True, "show_coef": False}
        if self._slopes_widget:
            try:
                toggles = self._slopes_widget.get_toggles()
            except Exception:
                pass
        
        show_base = toggles.get("show_base", True)
        show_adj = toggles.get("show_adj", True)
        show_coef = toggles.get("show_coef", False)

        # Calculate Coef line
        # Formula: Y = Y0 * (1 - C * (T0 - t))
        # This simplifies to a line passing through (T0, Y0)
        # Slope of this line wrt t is: dy/dt = Y0 * C
        # because Y = Y0 - Y0*C*T0 + Y0*C*t
        # So slope m_coef = Y0 * C
        # Wait, user formula: C = (1 - y/Y0) / (T0 - t)
        # => C * (T0 - t) = 1 - y/Y0
        # => y/Y0 = 1 - C*(T0 - t)
        # => y = Y0 * (1 - C*(T0 - t)) = Y0 - Y0*C*T0 + Y0*C*t
        # This is a line y = m*t + b where m = Y0*C and b = Y0 - Y0*C*T0
        
        # We need T0, Y0 (baseline) for this specific test run to plot the line correctly
        # We found them earlier for regression, let's try to extract them
        # Re-scan baseline pts (inefficient but safe)
        T0_plot, Y0_plot = 0.0, 0.0
        has_baseline = False
        if baseline_pts_for_plot:
            try:
                # Simple average for plot anchor
                T0_plot = sum(p[0] for p in baseline_pts_for_plot) / len(baseline_pts_for_plot)
                Y0_plot = sum(p[1] for p in baseline_pts_for_plot) / len(baseline_pts_for_plot)
                has_baseline = True
            except Exception:
                pass
        elif xs and ys:
             # If no baseline points found in 74-78, maybe just take the mean of the run?
             # Or just disable the line
             pass

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

            # Base Slope Line
            if show_base:
                try:
                    solid_ys = [b_solid + m_solid * t for t in xs]
                except Exception:
                    solid_ys = ys
                base_color = (180, 180, 255)
                solid_pen = self._pg.mkPen(color=base_color, width=2)  # type: ignore[attr-defined]
                self._plot_widget.plot(xs, solid_ys, pen=solid_pen)  # type: ignore[attr-defined]
            else:
                 # Need these for SSE calculation, so compute anyway but don't plot?
                 # Actually SSE calculation relies on them
                 try:
                    solid_ys = [b_solid + m_solid * t for t in xs]
                 except Exception:
                    solid_ys = ys

            # Adjusted Slope Line
            if show_adj:
                try:
                    dashed_ys = [b_dashed + m_dashed * t for t in xs]
                except Exception:
                    dashed_ys = ys
                base_color = (180, 180, 255)
                dashed_pen = self._pg.mkPen(
                    color=base_color, width=2, style=QtCore.Qt.DashLine
                )  # type: ignore[attr-defined]
                self._plot_widget.plot(xs, dashed_ys, pen=dashed_pen)  # type: ignore[attr-defined]
            else:
                 try:
                    dashed_ys = [b_dashed + m_dashed * t for t in xs]
                 except Exception:
                    dashed_ys = ys
            
            # Coef Line
            if show_coef and has_baseline and xs:
                # New logic: Connect Baseline point to a calculated point at min temp
                # 1. Baseline Anchor: (T0_plot, Y0_plot)
                # 2. Find min temp (t_min) in the data
                t_min = min(xs)
                
                # 3. Calculate what the value SHOULD be at t_min using the coefficient
                # Formula: Value = Raw * scale_factor
                # scale_factor = 1.0 - (dt * coefficient)
                # dt = (room_temp - t) ... wait, user formula was: dt = (room_temp_f - t)
                # In our context: T0 is the "room temp" / baseline temp (~76)
                
                dt = T0_plot - t_min
                scale_factor = 1.0 - (dt * c_line_slope)
                
                # We need the "Raw" value that would result in Y0 at T0. 
                # Actually, the logic is inverse: 
                # We want to show what the "ideal" line looks like.
                # If we are at T0, scale_factor is 1.0, so Value = Raw. 
                # So Y0 is our "Raw" reference? No, Y0 is the result of the scaling at T0.
                
                # Let's look at the user request:
                # "use the scalar to find what the scaled value should be at that point"
                
                # If we assume Y0 is the "correct" value we want to maintain...
                # And the sensor drifts by Coef % per degree.
                # The "Raw" reading at t_min would be: Y_min_expected
                
                # User said: Value = Raw * scale_factor
                # We want to plot the "model" line. 
                # At T0, Value = Y0.
                # At t_min, we predict Y_min.
                
                # Let's assume the user means:
                # "Show me the line that represents this coefficient behavior starting from the baseline."
                
                # If Value = Raw * (1 - (T0 - t)*C)
                # At T0, Value = Raw. So Raw = Y0.
                # At t_min, Value = Y0 * (1 - (T0 - t_min) * C)
                
                # Let's calculate that point:
                y_min_calc = Y0_plot * (1.0 - (dt * c_line_slope))
                
                print(f"[DEBUG] Plotting Coef Line:")
                print(f"  Phase: {phase_label}, Axis: {axis_label}, Sensor: {sensor_label}")
                print(f"  Baseline (T0, Y0): ({T0_plot:.2f}, {Y0_plot:.2f})")
                print(f"  Coef used: {c_line_slope:.6f}")
                print(f"  T_min: {t_min:.2f}, dt: {dt:.2f}")
                print(f"  Y_min_calc: {y_min_calc:.2f} (Expected)")
                
                try:
                    # Draw line from (t_min, y_min_calc) to (T0_plot, Y0_plot)
                    # We can extend it to max temp too for completeness
                    t_max = max(xs)
                    dt_max = T0_plot - t_max
                    y_max_calc = Y0_plot * (1.0 - (dt_max * c_line_slope))
                    
                    coef_xs = [t_min, T0_plot, t_max]
                    coef_ys = [y_min_calc, Y0_plot, y_max_calc]
                    
                    # Sort for plotting
                    coef_pts = sorted(zip(coef_xs, coef_ys), key=lambda p: p[0])
                    cx = [p[0] for p in coef_pts]
                    cy = [p[1] for p in coef_pts]

                    # Plot in a different color, e.g. Cyan or Orange
                    coef_pen = self._pg.mkPen(color=(255, 165, 0), width=2, style=QtCore.Qt.DashDotLine) # Orange
                    self._plot_widget.plot(cx, cy, pen=coef_pen)
                except Exception:
                    pass

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
                "coef_val": float(c_line_slope),
            }
            self.plot_metrics_updated.emit(metrics)
        except Exception:
            pass



