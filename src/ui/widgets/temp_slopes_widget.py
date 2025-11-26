from __future__ import annotations

from typing import Optional, Dict

from PySide6 import QtCore, QtWidgets


class TempSlopesWidget(QtWidgets.QWidget):
    """
    Compact replica of the legacy Temp Slopes tab UI.

    This widget is purely view-level: it exposes lightweight setters
    for slope/STD tables and for the "Current Plot" summary. All math
    is performed by the temp-plot/analysis layer.
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        def _lbl(text: str) -> QtWidgets.QLabel:
            lab = QtWidgets.QLabel(text)
            lab.setStyleSheet("color: rgb(220,220,230);")
            return lab

        def _make_axis_table(title: str) -> tuple[QtWidgets.QGroupBox, QtWidgets.QGridLayout]:
            box = QtWidgets.QGroupBox(f"{title} Axis")
            grid = QtWidgets.QGridLayout(box)
            grid.setContentsMargins(6, 6, 6, 6)
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(4)
            grid.addWidget(_lbl("Test"), 0, 0)
            grid.addWidget(_lbl("Slope"), 0, 1)
            grid.addWidget(_lbl("Std"), 0, 2)
            return box, grid

        # X axis table
        x_box, x_grid = _make_axis_table("X")
        x_grid.addWidget(_lbl("45 lb"), 1, 0)
        self.lbl_slope_db_x = _lbl("—")
        self.lbl_std_db_x = _lbl("—")
        x_grid.addWidget(self.lbl_slope_db_x, 1, 1)
        x_grid.addWidget(self.lbl_std_db_x, 1, 2)
        x_grid.addWidget(_lbl("Bodyweight"), 2, 0)
        self.lbl_slope_bw_x = _lbl("—")
        self.lbl_std_bw_x = _lbl("—")
        x_grid.addWidget(self.lbl_slope_bw_x, 2, 1)
        x_grid.addWidget(self.lbl_std_bw_x, 2, 2)
        x_grid.addWidget(_lbl("All Tests"), 3, 0)
        self.lbl_slope_all_x = _lbl("—")
        self.lbl_std_all_x = _lbl("—")
        x_grid.addWidget(self.lbl_slope_all_x, 3, 1)
        x_grid.addWidget(self.lbl_std_all_x, 3, 2)

        # Y axis table
        y_box, y_grid = _make_axis_table("Y")
        y_grid.addWidget(_lbl("45 lb"), 1, 0)
        self.lbl_slope_db_y = _lbl("—")
        self.lbl_std_db_y = _lbl("—")
        y_grid.addWidget(self.lbl_slope_db_y, 1, 1)
        y_grid.addWidget(self.lbl_std_db_y, 1, 2)
        y_grid.addWidget(_lbl("Bodyweight"), 2, 0)
        self.lbl_slope_bw_y = _lbl("—")
        self.lbl_std_bw_y = _lbl("—")
        y_grid.addWidget(self.lbl_slope_bw_y, 2, 1)
        y_grid.addWidget(self.lbl_std_bw_y, 2, 2)
        y_grid.addWidget(_lbl("All Tests"), 3, 0)
        self.lbl_slope_all_y = _lbl("—")
        self.lbl_std_all_y = _lbl("—")
        y_grid.addWidget(self.lbl_slope_all_y, 3, 1)
        y_grid.addWidget(self.lbl_std_all_y, 3, 2)

        # Z axis table
        z_box, z_grid = _make_axis_table("Z")
        z_grid.addWidget(_lbl("45 lb"), 1, 0)
        self.lbl_slope_db_z = _lbl("—")
        self.lbl_std_db_z = _lbl("—")
        z_grid.addWidget(self.lbl_slope_db_z, 1, 1)
        z_grid.addWidget(self.lbl_std_db_z, 1, 2)
        z_grid.addWidget(_lbl("Bodyweight"), 2, 0)
        self.lbl_slope_bw_z = _lbl("—")
        self.lbl_std_bw_z = _lbl("—")
        z_grid.addWidget(self.lbl_slope_bw_z, 2, 1)
        z_grid.addWidget(self.lbl_std_bw_z, 2, 2)
        z_grid.addWidget(_lbl("All Tests"), 3, 0)
        self.lbl_slope_all_z = _lbl("—")
        self.lbl_std_all_z = _lbl("—")
        z_grid.addWidget(self.lbl_slope_all_z, 3, 1)
        z_grid.addWidget(self.lbl_std_all_z, 3, 2)

        # Arrange raw slope tables
        root.addWidget(x_box)
        root.addWidget(y_box)
        root.addWidget(z_box)

        # Current-plot adjustment summary table
        current_box = QtWidgets.QGroupBox("Current Plot")
        cgrid = QtWidgets.QGridLayout(current_box)
        cgrid.setContentsMargins(6, 6, 6, 6)
        cgrid.setHorizontalSpacing(10)
        cgrid.setVerticalSpacing(4)
        cgrid.addWidget(_lbl("Base slope (solid line)"), 0, 0)
        self.lbl_plot_base_slope = _lbl("—")
        cgrid.addWidget(self.lbl_plot_base_slope, 0, 1)
        cgrid.addWidget(_lbl("Multiplier"), 1, 0)
        self.lbl_plot_multiplier = _lbl("—")
        cgrid.addWidget(self.lbl_plot_multiplier, 1, 1)
        cgrid.addWidget(_lbl("Adj slope (dashed line)"), 2, 0)
        self.lbl_plot_adj_slope = _lbl("—")
        cgrid.addWidget(self.lbl_plot_adj_slope, 2, 1)
        cgrid.addWidget(_lbl("% better vs base (SSE)"), 3, 0)
        self.lbl_plot_improve_pct = _lbl("—")
        cgrid.addWidget(self.lbl_plot_improve_pct, 3, 1)
        root.addWidget(current_box)

        root.addStretch(1)

    # --- Public API ---------------------------------------------------------

    def set_slopes(self, avgs: Dict[str, Dict[str, float]], stds: Dict[str, Dict[str, float]]) -> None:
        """
        Update the slope/STD tables for X/Y/Z axes.

        avgs/stds are expected in the format:
          { 'bodyweight': {'x': float, 'y': float, 'z': float},
            '45lb': {'x': ..., 'y': ..., 'z': ...},
            'all': {'x': ..., 'y': ..., 'z': ...} }
        """

        def _get(ph: str, ax: str) -> float:
            try:
                return float((avgs or {}).get(ph, {}).get(ax, 0.0))
            except Exception:
                return 0.0

        def _get_std(ph: str, ax: str) -> float:
            try:
                return float((stds or {}).get(ph, {}).get(ax, 0.0))
            except Exception:
                return 0.0

        try:
            self.lbl_slope_db_x.setText(f"{_get('45lb', 'x'):.6f}")
            self.lbl_std_db_x.setText(f"{_get_std('45lb', 'x'):.6f}")
            self.lbl_slope_bw_x.setText(f"{_get('bodyweight', 'x'):.6f}")
            self.lbl_std_bw_x.setText(f"{_get_std('bodyweight', 'x'):.6f}")
            self.lbl_slope_all_x.setText(f"{_get('all', 'x'):.6f}")
            self.lbl_std_all_x.setText(f"{_get_std('all', 'x'):.6f}")
        except Exception:
            pass

        try:
            self.lbl_slope_db_y.setText(f"{_get('45lb', 'y'):.6f}")
            self.lbl_std_db_y.setText(f"{_get_std('45lb', 'y'):.6f}")
            self.lbl_slope_bw_y.setText(f"{_get('bodyweight', 'y'):.6f}")
            self.lbl_std_bw_y.setText(f"{_get_std('bodyweight', 'y'):.6f}")
            self.lbl_slope_all_y.setText(f"{_get('all', 'y'):.6f}")
            self.lbl_std_all_y.setText(f"{_get_std('all', 'y'):.6f}")
        except Exception:
            pass

        try:
            self.lbl_slope_db_z.setText(f"{_get('45lb', 'z'):.6f}")
            self.lbl_std_db_z.setText(f"{_get_std('45lb', 'z'):.6f}")
            self.lbl_slope_bw_z.setText(f"{_get('bodyweight', 'z'):.6f}")
            self.lbl_std_bw_z.setText(f"{_get_std('bodyweight', 'z'):.6f}")
            self.lbl_slope_all_z.setText(f"{_get('all', 'z'):.6f}")
            self.lbl_std_all_z.setText(f"{_get_std('all', 'z'):.6f}")
        except Exception:
            pass

    def set_current_plot_stats(self, metrics: Optional[Dict[str, float]]) -> None:
        """
        Update the 'Current Plot' summary from a metrics dict:
          {
            'base': float or None,
            'mult': float or None,
            'adj': float or None,
            'improve_pct': float or None,
            'a': float or None,
            'b': float or None,
            'Fref': float or None,
            'is_sum': bool
          }
        """
        m = metrics or {}
        base = m.get("base")
        mult = m.get("mult")
        adj = m.get("adj")
        imp = m.get("improve_pct")
        a = m.get("a")
        b = m.get("b")
        Fref = m.get("Fref")
        is_sum = bool(m.get("is_sum", False))

        try:
            if base is None:
                self.lbl_plot_base_slope.setText("—")
            else:
                val = float(base)
                if is_sum:
                    val /= 8.0
                self.lbl_plot_base_slope.setText(f"{val:.6f}")
        except Exception:
            pass

        try:
            if mult is None:
                self.lbl_plot_multiplier.setText("—")
            else:
                if a is None or b is None or Fref is None:
                    self.lbl_plot_multiplier.setText(f"k = {float(mult):.4f}")
                else:
                    self.lbl_plot_multiplier.setText(
                        f"k = {float(mult):.4f} = {float(a):.4f} + {float(b):.6f} * {float(Fref):.2f}"
                    )
        except Exception:
            pass

        try:
            if adj is None:
                self.lbl_plot_adj_slope.setText("—")
            else:
                val = float(adj)
                if is_sum:
                    val /= 8.0
                self.lbl_plot_adj_slope.setText(f"{val:.6f}")
        except Exception:
            pass

        try:
            if imp is None:
                self.lbl_plot_improve_pct.setText("—")
            else:
                self.lbl_plot_improve_pct.setText(f"{float(imp):.1f}%")
        except Exception:
            pass



