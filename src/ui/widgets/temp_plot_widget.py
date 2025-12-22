from __future__ import annotations

from typing import Optional, List, Tuple
import os
import csv
import io
import json

from PySide6 import QtCore, QtWidgets, QtGui

from ... import config
from ...services.backend_csv_processor import process_csv_via_backend
from ..discrete_temp.coef_math import compute_baseline_anchor, estimate_coefs, summarize, coef_line_points
from .temp_coef_widget import TempCoefWidget


class TempPlotWidget(QtWidgets.QWidget):
    """
    Temperature-vs-force plot for discrete temperature testing.

    Focused discrete-temp visualization:
      - Choose phase (45 lb vs Bodyweight), sensor, and axis
      - Plot raw `discrete_temp_session.csv` values
      - Optionally overlay backend-NN processed traces (temp correction off/on)
    """

    _processed_ready = QtCore.Signal(object)  # internal: emitted by worker on completion

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None, hardware_service: object | None = None) -> None:
        super().__init__(parent)

        self._hardware = hardware_service
        self._csv_path: str = ""
        self._measurement_csv_path: str = ""

        # NN processed overlays
        self._nn_off_csv_path: str = ""
        self._nn_on_csv_path: str = ""
        self._show_nn_off: bool = True
        self._show_nn_on: bool = True
        self._worker: Optional[QtCore.QThread] = None
        self._coef_widget: Optional[TempCoefWidget] = None
        self._show_coef_line: bool = False

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # Try to use pyqtgraph backend
        self._pg = None  # type: ignore[assignment]
        self._plot_widget: Optional[QtWidgets.QWidget] = None
        self._nn_viewbox = None
        self._nn_curves: list[object] = []
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

            # Right-side axis for NN overlays (independent y-scale)
            try:
                plot_item = self._plot_widget.plotItem  # type: ignore[attr-defined]
                plot_item.showAxis("right")
                plot_item.getAxis("right").setLabel("NN Output")  # type: ignore[attr-defined]

                self._nn_viewbox = pg.ViewBox()
                plot_item.scene().addItem(self._nn_viewbox)
                plot_item.getAxis("right").linkToView(self._nn_viewbox)  # type: ignore[attr-defined]
                self._nn_viewbox.setXLink(plot_item.vb)

                def _update_views():
                    try:
                        self._nn_viewbox.setGeometry(plot_item.vb.sceneBoundingRect())
                        self._nn_viewbox.linkedViewChanged(plot_item.vb, self._nn_viewbox.XAxis)
                    except Exception:
                        pass

                plot_item.vb.sigResized.connect(_update_views)
                _update_views()
                # Hide right axis until we have NN data
                plot_item.getAxis("right").setVisible(False)  # type: ignore[attr-defined]
            except Exception:
                self._nn_viewbox = None
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
        # Overlay toggles (enabled after processing)
        self.chk_nn_off = QtWidgets.QCheckBox("NN Off")
        self.chk_nn_on = QtWidgets.QCheckBox("NN Corrected")
        self.chk_nn_off.setChecked(True)
        self.chk_nn_on.setChecked(True)
        self.chk_nn_off.setEnabled(False)
        self.chk_nn_on.setEnabled(False)
        ctrl_row.addWidget(self.chk_nn_off)
        ctrl_row.addWidget(self.chk_nn_on)
        ctrl_row.addStretch(1)
        root.addLayout(ctrl_row)

        # Re-plot when any Temp Plot setting changes (if a test is selected)
        try:
            self.phase_combo.currentIndexChanged.connect(lambda _i: self.plot_current())
            self.sensor_combo.currentIndexChanged.connect(lambda _i: self.plot_current())
            self.axis_combo.currentIndexChanged.connect(lambda _i: self.plot_current())
            self.chk_nn_off.toggled.connect(self._on_toggle_nn_off)
            self.chk_nn_on.toggled.connect(self._on_toggle_nn_on)
        except Exception:
            pass

        self._processed_ready.connect(self._on_processed_ready)

    # --- Public API ---------------------------------------------------------

    def set_coef_widget(self, widget: TempCoefWidget) -> None:
        self._coef_widget = widget
        try:
            widget.toggles_changed.connect(self._on_coef_toggles_changed)
        except Exception:
            pass

    @QtCore.Slot(str)
    def set_test_path(self, path: str) -> None:
        """Set the active discrete_temp_session.csv file (folder or file path)."""
        # Caller will typically pass the folder; we normalize to CSV path here.
        p = str(path or "").strip()

        def _clear():
            self._csv_path = ""
            self._measurement_csv_path = ""
            self._nn_off_csv_path = ""
            self._nn_on_csv_path = ""
            try:
                self.chk_nn_off.blockSignals(True)
                self.chk_nn_on.blockSignals(True)
                self.chk_nn_off.setEnabled(False)
                self.chk_nn_on.setEnabled(False)
                self.chk_nn_off.setChecked(False)
                self.chk_nn_on.setChecked(False)
                self._show_nn_off = False
                self._show_nn_on = False
            finally:
                try:
                    self.chk_nn_off.blockSignals(False)
                    self.chk_nn_on.blockSignals(False)
                except Exception:
                    pass
            if self._plot_widget is not None:
                try:
                    self._plot_widget.clear()
                except Exception:
                    pass

        if not p:
            _clear()
            return

        if os.path.isdir(p):
            base_dir = p
        else:
            base_dir = os.path.dirname(p)

        # Always treat measurements as belonging to the test folder.
        # We choose discrete_temp_session.csv as the canonical source for calculations,
        # and discrete_temp_measurements.csv as plot-only overlay data.
        session_candidate = os.path.join(base_dir, "discrete_temp_session.csv")
        measurements_candidate = os.path.join(base_dir, "discrete_temp_measurements.csv")

        # If caller passed an explicit session file path, honor it. Otherwise, use folder candidate.
        if os.path.isfile(p) and os.path.basename(p).lower() == "discrete_temp_session.csv":
            self._csv_path = p
        else:
            self._csv_path = session_candidate if os.path.isfile(session_candidate) else ""

        self._measurement_csv_path = (
            measurements_candidate if os.path.isfile(measurements_candidate) else ""
        )

        if not self._csv_path:
            _clear()
        else:
            # On selection change: probe for existing NN processed files in this folder.
            # If they aren't present, disable NN toggles (even if previously enabled from another test).
            try:
                nn_off = os.path.join(base_dir, "discrete_temp_session__nn_off.csv")
                nn_scalar_candidates: list[str] = []
                for fn in os.listdir(base_dir):
                    fn_lc = fn.lower()
                    if fn_lc.startswith("discrete_temp_session__nn_scalar_") and fn_lc.endswith(".csv"):
                        nn_scalar_candidates.append(os.path.join(base_dir, fn))
                nn_on = ""
                if nn_scalar_candidates:
                    nn_on = max(nn_scalar_candidates, key=lambda p: os.path.getmtime(p))

                self._nn_off_csv_path = nn_off if os.path.isfile(nn_off) else ""
                self._nn_on_csv_path = nn_on if (nn_on and os.path.isfile(nn_on)) else ""

                # Apply enable/checked state based on what we found.
                try:
                    self.chk_nn_off.blockSignals(True)
                    self.chk_nn_on.blockSignals(True)

                    has_off = bool(self._nn_off_csv_path)
                    has_on = bool(self._nn_on_csv_path)
                    self.chk_nn_off.setEnabled(has_off)
                    self.chk_nn_on.setEnabled(has_on)

                    # If a file is missing, force it off.
                    if not has_off:
                        self.chk_nn_off.setChecked(False)
                        self._show_nn_off = False
                    else:
                        # Default on when available
                        self.chk_nn_off.setChecked(True)
                        self._show_nn_off = True

                    if not has_on:
                        self.chk_nn_on.setChecked(False)
                        self._show_nn_on = False
                    else:
                        self.chk_nn_on.setChecked(True)
                        self._show_nn_on = True
                finally:
                    try:
                        self.chk_nn_off.blockSignals(False)
                        self.chk_nn_on.blockSignals(False)
                    except Exception:
                        pass
            except Exception:
                # If anything goes sideways probing the folder, err on the safe side: no NN overlays
                self._nn_off_csv_path = ""
                self._nn_on_csv_path = ""
                try:
                    self.chk_nn_off.setEnabled(False)
                    self.chk_nn_on.setEnabled(False)
                    self.chk_nn_off.setChecked(False)
                    self.chk_nn_on.setChecked(False)
                except Exception:
                    pass

            # Auto-plot when a valid test is selected
            self.plot_current()

    @QtCore.Slot()
    def process_current(self) -> None:
        """
        Process the currently selected discrete_temp_session.csv through the backend NN:
        - once with temp correction off
        - once with scalar coefficients on (X/Y/Z)
        Then overlay the processed traces on the plot.
        """
        if not self._csv_path:
            return

        base_dir = os.path.dirname(self._csv_path)
        meta_path = os.path.join(base_dir, "test_meta.json")
        device_id = ""
        try:
            if os.path.isfile(meta_path):
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f) or {}
                device_id = str(meta.get("device_id") or meta.get("deviceId") or "").strip()
        except Exception:
            device_id = ""

        if not device_id:
            # Fallback: parse from CSV first data row
            try:
                with open(self._csv_path, "r", encoding="utf-8", newline="") as f:
                    header_line = f.readline()
                    header_reader = csv.reader(io.StringIO(header_line))
                    headers = [h.strip() for h in next(header_reader, [])]
                    reader = csv.DictReader(f, fieldnames=headers, skipinitialspace=True)
                    first = next(reader, None) or {}
                    device_id = str(first.get("device_id") or first.get("deviceId") or "").strip()
            except Exception:
                device_id = ""

        if not device_id:
            return

        # Use per-file, Sum-sensor "All" coefficients for processing (preferred),
        # falling back to config defaults if anything is missing.
        defaults = {"x": float(config.DISCRETE_TEMP_COEF_X), "y": float(config.DISCRETE_TEMP_COEF_Y), "z": float(config.DISCRETE_TEMP_COEF_Z)}
        try:
            table = self._sum_sensor_coef_table(self._csv_path)
            all_coefs = dict((table or {}).get("all") or {})
        except Exception:
            all_coefs = {}
        coeffs = {
            "x": float(all_coefs.get("x") if all_coefs.get("x") not in (None, 0.0) else defaults["x"]),
            "y": float(all_coefs.get("y") if all_coefs.get("y") not in (None, 0.0) else defaults["y"]),
            "z": float(all_coefs.get("z") if all_coefs.get("z") not in (None, 0.0) else defaults["z"]),
        }

        def _fmt(val: float) -> str:
            try:
                s = f"{float(val):.4f}".rstrip("0").rstrip(".")
                if not s:
                    s = "0"
                if "." not in s:
                    s = f"{s}.0"
                return s
            except Exception:
                return "0.0"

        coef_tag = "_".join([_fmt(coeffs["x"]), _fmt(coeffs["y"]), _fmt(coeffs["z"])])
        out_off = "discrete_temp_session__nn_off.csv"
        out_on = f"discrete_temp_session__nn_scalar_{coef_tag}.csv"

        class _Worker(QtCore.QThread):
            def __init__(self, parent: QtCore.QObject):
                super().__init__(parent)
                self.err: Optional[str] = None
                self.off_path: str = ""
                self.on_path: str = ""

            def run(self) -> None:
                try:
                    self.off_path = process_csv_via_backend(
                        input_csv_path=self.parent()._csv_path,  # type: ignore[attr-defined]
                        device_id=device_id,
                        output_folder=base_dir,
                        output_filename=out_off,
                        use_temperature_correction=False,
                        room_temp_f=76.0,
                        mode="scalar",
                        temperature_coefficients=None,
                        sanitize_header=True,
                        hardware=self.parent()._hardware,  # type: ignore[attr-defined]
                        timeout_s=300,
                    )
                    self.on_path = process_csv_via_backend(
                        input_csv_path=self.parent()._csv_path,  # type: ignore[attr-defined]
                        device_id=device_id,
                        output_folder=base_dir,
                        output_filename=out_on,
                        use_temperature_correction=True,
                        room_temp_f=76.0,
                        mode="scalar",
                        temperature_coefficients=coeffs,
                        sanitize_header=True,
                        hardware=self.parent()._hardware,  # type: ignore[attr-defined]
                        timeout_s=300,
                    )
                except Exception as e:
                    self.err = str(e)

        # Cancel prior worker if still running
        try:
            if self._worker is not None and self._worker.isRunning():
                self._worker.requestInterruption()
        except Exception:
            pass

        w = _Worker(self)
        self._worker = w

        def _done():
            payload = {"error": getattr(w, "err", None), "off": getattr(w, "off_path", ""), "on": getattr(w, "on_path", "")}
            self._processed_ready.emit(payload)

        w.finished.connect(_done)
        w.start()

    @QtCore.Slot()
    def plot_current(self) -> None:
        """Plot temperature vs force for the currently selected discrete test."""
        if not self._csv_path or self._plot_widget is None or self._pg is None:
            return

        self._plot(self._csv_path, self._measurement_csv_path)

    # --- Internal helpers ---------------------------------------------------

    def _read_points(self, csv_path: str, phase_name: str, col_name: str) -> Tuple[List[float], List[float]]:
        xs: List[float] = []
        ys: List[float] = []
        if not csv_path or not os.path.isfile(csv_path) or os.path.getsize(csv_path) <= 0:
            return xs, ys
        try:
            with open(csv_path, "r", encoding="utf-8", newline="") as f:
                header_line = f.readline()
                if not header_line:
                    return xs, ys
                header_reader = csv.reader(io.StringIO(header_line))
                headers = [h.strip() for h in next(header_reader, [])]
                reader = csv.DictReader(f, fieldnames=headers, skipinitialspace=True)
                for row in reader:
                    if not row:
                        continue
                    ph = str(row.get("phase_name") or row.get("phase") or "").strip().lower()
                    if ph != phase_name:
                        continue
                    try:
                        temp_f = float(row.get("sum-t") or 0.0)
                        y_val = float(row.get(col_name) or 0.0)
                    except Exception:
                        continue
                    xs.append(temp_f)
                    ys.append(y_val)
        except Exception:
            return [], []
        pts = sorted(zip(xs, ys), key=lambda p: p[0])
        return [p[0] for p in pts], [p[1] for p in pts]

    def _plot(self, csv_path: str, measurement_csv_path: str = "") -> None:
        assert self._plot_widget is not None and self._pg is not None

        phase_label = str(self.phase_combo.currentText() or "Bodyweight").strip().lower()
        phase_name = "45lb" if phase_label.startswith("45") else "bodyweight"

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

        # Raw session points (no NN)
        xs, ys = self._read_points(csv_path, phase_name, col_name)

        # Optional overlay: discrete_temp_measurements.csv (plot-only)
        mxs: List[float] = []
        mys: List[float] = []
        if measurement_csv_path and os.path.isfile(measurement_csv_path) and os.path.getsize(measurement_csv_path) > 0:
            mxs, mys = self._read_points(measurement_csv_path, phase_name, col_name)

        # NN overlays
        nn_off_xs: List[float] = []
        nn_off_ys: List[float] = []
        nn_on_xs: List[float] = []
        nn_on_ys: List[float] = []
        if self._nn_off_csv_path and self._show_nn_off:
            nn_off_xs, nn_off_ys = self._read_points(self._nn_off_csv_path, phase_name, col_name)
        if self._nn_on_csv_path and self._show_nn_on:
            nn_on_xs, nn_on_ys = self._read_points(self._nn_on_csv_path, phase_name, col_name)

        if not xs and not nn_off_xs and not nn_on_xs:
            return

        try:
            # Clear left plot and NN overlay viewbox
            self._plot_widget.clear()  # type: ignore[union-attr]
            if self._nn_viewbox is not None:
                try:
                    self._nn_viewbox.clear()
                except Exception:
                    # Some pyqtgraph versions don't expose clear(); fall back to removing items
                    try:
                        for c in list(self._nn_curves):
                            try:
                                self._nn_viewbox.removeItem(c)
                            except Exception:
                                pass
                    except Exception:
                        pass
                self._nn_curves = []

            plot_item = getattr(self._plot_widget, "plotItem", None)
            try:
                axis_label_full = axis_label.upper()
                self._plot_widget.setLabel("bottom", "Temperature (°F)")  # type: ignore[attr-defined]
                self._plot_widget.setLabel("left", f"{sensor_label} {axis_label_full} (raw)")  # type: ignore[attr-defined]
                if plot_item is not None:
                    plot_item.getAxis("right").setLabel(f"{sensor_label} {axis_label_full} (NN)")  # type: ignore[attr-defined]
            except Exception:
                pass

            # Measurements overlay (gray dots)
            try:
                if mxs and mys and len(mxs) == len(mys):
                    overlay_brush = self._pg.mkBrush(180, 180, 180, 255)
                    overlay_pen = self._pg.mkPen(color=(80, 80, 80, 255), width=1)
                    self._plot_widget.plot(  # type: ignore[attr-defined]
                        mxs,
                        mys,
                        pen=None,
                        symbol="o",
                        symbolSize=5,
                        symbolBrush=overlay_brush,
                        symbolPen=overlay_pen,
                    )
            except Exception:
                pass

            # Raw (green)
            # NOTE: Left axis scale should always be driven by the raw/original series so it stays
            # centered and clearly visible, regardless of NN overlay visibility/toggles.
            raw_y_for_range: List[float] = []
            x_for_range: List[float] = []
            if xs and ys:
                self._plot_widget.plot(  # type: ignore[attr-defined]
                    xs,
                    ys,
                    pen=self._pg.mkPen(color=(120, 220, 120), width=2),
                    symbol="o",
                    symbolBrush=(200, 250, 200),
                    symbolSize=7,
                )
                raw_y_for_range = list(ys)
                x_for_range.extend(list(xs))

            # Coef line overlay (raw axis), controlled by TempCoefWidget toggle
            try:
                if xs and ys and self._show_coef_line:
                    pts = list(zip(xs, ys))
                    anchor = compute_baseline_anchor(pts)
                    coefs = estimate_coefs(pts, anchor)
                    stats = summarize(coefs)
                    if stats.n > 0:
                        t_min = min(xs)
                        t_max = max(xs)
                        line_pts = coef_line_points(anchor=anchor, coef=stats.mean, t_values=[t_min, anchor.t0, t_max])
                        cx = [p[0] for p in line_pts]
                        cy = [p[1] for p in line_pts]
                        self._plot_widget.plot(  # type: ignore[attr-defined]
                            cx,
                            cy,
                            pen=self._pg.mkPen(color=(255, 165, 0), width=2, style=QtCore.Qt.DashDotLine),
                        )
                        # Coef line is part of the raw-axis overlay; include it in left-axis scaling
                        # without letting NN overlays influence the raw axis range.
                        try:
                            raw_y_for_range.extend([float(v) for v in cy])
                        except Exception:
                            pass
            except Exception:
                pass

            # NN overlays (right axis)
            show_nn_axis = False
            if self._nn_viewbox is not None:
                # NN off (blue)
                if nn_off_xs and nn_off_ys:
                    show_nn_axis = True
                    try:
                        c = self._pg.PlotDataItem(  # type: ignore[attr-defined]
                            nn_off_xs,
                            nn_off_ys,
                            pen=self._pg.mkPen(color=(120, 180, 255), width=2),
                            symbol="t",
                            symbolBrush=(120, 180, 255),
                            symbolSize=7,
                        )
                        self._nn_viewbox.addItem(c)
                        self._nn_curves.append(c)
                    except Exception:
                        pass
                    try:
                        x_for_range.extend(list(nn_off_xs))
                    except Exception:
                        pass

                # NN corrected (orange)
                if nn_on_xs and nn_on_ys:
                    show_nn_axis = True
                    try:
                        c = self._pg.PlotDataItem(  # type: ignore[attr-defined]
                            nn_on_xs,
                            nn_on_ys,
                            pen=self._pg.mkPen(color=(255, 165, 0), width=2),
                            symbol="s",
                            symbolBrush=(255, 165, 0),
                            symbolSize=7,
                        )
                        self._nn_viewbox.addItem(c)
                        self._nn_curves.append(c)
                    except Exception:
                        pass
                    try:
                        x_for_range.extend(list(nn_on_xs))
                    except Exception:
                        pass

                # Autoscale NN viewbox independently (Y only)
                try:
                    if show_nn_axis:
                        self._nn_viewbox.enableAutoRange(axis=self._pg.ViewBox.YAxis, enable=True)
                        self._nn_viewbox.autoRange()
                except Exception:
                    pass

            # Toggle right axis visibility based on whether we have NN data to show
            try:
                if plot_item is not None:
                    plot_item.getAxis("right").setVisible(bool(show_nn_axis))  # type: ignore[attr-defined]
            except Exception:
                pass

            # Always scale the X axis to fit all *visible* plotted points (raw + measurement overlay + NN).
            # This prevents carrying over a previous file's zoom/range when switching selections.
            try:
                if mxs:
                    x_for_range.extend(list(mxs))
            except Exception:
                pass
            try:
                if plot_item is not None and x_for_range:
                    x_min = min(x_for_range)
                    x_max = max(x_for_range)
                    if x_min == x_max:
                        x_pad = max(1.0, abs(x_min) * 0.02)
                    else:
                        x_pad = (x_max - x_min) * 0.03
                    x_lo = x_min - x_pad
                    x_hi = x_max + x_pad
                    plot_item.vb.enableAutoRange(axis=self._pg.ViewBox.XAxis, enable=False)
                    try:
                        plot_item.vb.setRange(xRange=(x_lo, x_hi), padding=0.0)
                    except Exception:
                        try:
                            plot_item.vb.setXRange(x_lo, x_hi, padding=0.0)
                        except Exception:
                            pass
            except Exception:
                pass

            # Always scale the LEFT (raw) axis from the raw/original series. This takes priority
            # over NN overlays (which are always on the right axis / separate ViewBox).
            try:
                if plot_item is not None and raw_y_for_range:
                    y_min = min(raw_y_for_range)
                    y_max = max(raw_y_for_range)
                    if y_min == y_max:
                        pad = max(1.0, abs(y_min) * 0.05)
                    else:
                        pad = (y_max - y_min) * 0.08
                    lo = y_min - pad
                    hi = y_max + pad
                    # Set explicit y-range so raw is always visible, even if the user previously zoomed.
                    plot_item.vb.enableAutoRange(axis=self._pg.ViewBox.YAxis, enable=False)
                    try:
                        plot_item.vb.setRange(yRange=(lo, hi), padding=0.0)
                    except Exception:
                        # Fallback for older pyqtgraph versions
                        try:
                            plot_item.vb.setYRange(lo, hi, padding=0.0)
                        except Exception:
                            pass
            except Exception:
                pass
        except Exception:
            pass

        # Update coef widget metrics (table + current selection stats)
        if self._coef_widget is not None:
            try:
                self._coef_widget.set_current_selection_stats(self._current_selection_coef_stats())
                self._coef_widget.set_coef_table(self._sum_sensor_coef_table(csv_path))
            except Exception:
                pass

    def _sum_sensor_coef_table(self, csv_path: str) -> dict:
        # Compute coefs for Sum sensor across both phases and axes.
        phases = ["45lb", "bodyweight"]
        axes = ["x", "y", "z"]
        out = {"45lb": {}, "bodyweight": {}, "all": {}}

        # Per-phase
        for ph in phases:
            for ax in axes:
                xs, ys = self._read_points(csv_path, ph, f"sum-{ax}")
                pts = list(zip(xs, ys))
                anchor = compute_baseline_anchor(pts)
                stats = summarize(estimate_coefs(pts, anchor))
                out[ph][ax] = float(stats.mean) if stats.n else 0.0

        # "All" is the simple average of phase means: (45lb + bodyweight) / 2 per axis.
        # This is intentionally NOT a refit on concatenated points, so the two phases
        # contribute equally even if they have different numbers of samples.
        for ax in axes:
            v45 = float(out.get("45lb", {}).get(ax, 0.0) or 0.0)
            vbw = float(out.get("bodyweight", {}).get(ax, 0.0) or 0.0)
            # If one phase is missing, fall back to the other (keeps UI informative).
            if v45 and vbw:
                out["all"][ax] = (v45 + vbw) / 2.0
            else:
                out["all"][ax] = v45 or vbw or 0.0

        return out

    def _current_selection_coef_stats(self) -> dict:
        # Compute coef stats for currently selected phase/sensor/axis from raw data.
        if not self._csv_path:
            return {}
        phase_label = str(self.phase_combo.currentText() or "Bodyweight").strip().lower()
        phase_name = "45lb" if phase_label.startswith("45") else "bodyweight"
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
        xs, ys = self._read_points(self._csv_path, phase_name, f"{prefix}-{axis_label}")
        pts = list(zip(xs, ys))
        anchor = compute_baseline_anchor(pts)
        stats = summarize(estimate_coefs(pts, anchor))
        return {"t0": anchor.t0, "y0": anchor.y0, "coef_mean": stats.mean if stats.n else None, "n": stats.n}

    def _on_coef_toggles_changed(self) -> None:
        try:
            self._show_coef_line = bool((self._coef_widget.get_toggles() if self._coef_widget else {}).get("show_coef", False))
        except Exception:
            self._show_coef_line = False
        self.plot_current()

    def _on_processed_ready(self, payload: object) -> None:
        try:
            p = dict(payload or {})
        except Exception:
            p = {}
        err = p.get("error")
        if err:
            return
        self._nn_off_csv_path = str(p.get("off") or "")
        self._nn_on_csv_path = str(p.get("on") or "")
        # Enable toggles now that we have processed traces
        try:
            self.chk_nn_off.setEnabled(bool(self._nn_off_csv_path))
            self.chk_nn_on.setEnabled(bool(self._nn_on_csv_path))
        except Exception:
            pass
        self.plot_current()

    def _on_toggle_nn_off(self, v: bool) -> None:
        self._show_nn_off = bool(v)
        self.plot_current()

    def _on_toggle_nn_on(self, v: bool) -> None:
        self._show_nn_on = bool(v)
        self.plot_current()
