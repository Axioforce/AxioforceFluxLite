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
    
    # Signals to notify plot widget of toggle changes
    toggles_changed = QtCore.Signal()

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)


        def _lbl(text: str) -> QtWidgets.QLabel:
            lab = QtWidgets.QLabel(text)
            lab.setStyleSheet("color: rgb(220,220,230);")
            return lab

        # --- Left Column: Slope/Std Tables ---
        left_layout = QtWidgets.QVBoxLayout()
        left_layout.setSpacing(6)
        
        # --- Right Column: Coef Tables ---
        right_layout = QtWidgets.QVBoxLayout()
        right_layout.setSpacing(6)

        def _make_axis_table(title: str, is_coef: bool = False) -> tuple[QtWidgets.QGroupBox, QtWidgets.QGridLayout]:
            label = "Coef" if is_coef else "Slope Analysis"
            box = QtWidgets.QGroupBox(f"{title} Axis - {label}")
            grid = QtWidgets.QGridLayout(box)
            grid.setContentsMargins(6, 6, 6, 6)
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(4)
            if is_coef:
                grid.addWidget(_lbl("Test"), 0, 0)
                grid.addWidget(_lbl("Coef"), 0, 1)
            else:
                grid.addWidget(_lbl("Test"), 0, 0)
                grid.addWidget(_lbl("Slope"), 0, 1)
                grid.addWidget(_lbl("Std"), 0, 2)
            return box, grid

        # X axis table (Slope)
        x_box, x_grid = _make_axis_table("X", is_coef=False)
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
        left_layout.addWidget(x_box)

        # X axis table (Coef)
        x_coef_box, x_coef_grid = _make_axis_table("X", is_coef=True)
        x_coef_grid.addWidget(_lbl("45 lb"), 1, 0)
        self.lbl_coef_db_x = _lbl("—")
        x_coef_grid.addWidget(self.lbl_coef_db_x, 1, 1)
        x_coef_grid.addWidget(_lbl("Bodyweight"), 2, 0)
        self.lbl_coef_bw_x = _lbl("—")
        x_coef_grid.addWidget(self.lbl_coef_bw_x, 2, 1)
        x_coef_grid.addWidget(_lbl("All Tests"), 3, 0)
        self.lbl_coef_all_x = _lbl("—")
        x_coef_grid.addWidget(self.lbl_coef_all_x, 3, 1)
        right_layout.addWidget(x_coef_box)

        # Y axis table (Slope)
        y_box, y_grid = _make_axis_table("Y", is_coef=False)
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
        left_layout.addWidget(y_box)

        # Y axis table (Coef)
        y_coef_box, y_coef_grid = _make_axis_table("Y", is_coef=True)
        y_coef_grid.addWidget(_lbl("45 lb"), 1, 0)
        self.lbl_coef_db_y = _lbl("—")
        y_coef_grid.addWidget(self.lbl_coef_db_y, 1, 1)
        y_coef_grid.addWidget(_lbl("Bodyweight"), 2, 0)
        self.lbl_coef_bw_y = _lbl("—")
        y_coef_grid.addWidget(self.lbl_coef_bw_y, 2, 1)
        y_coef_grid.addWidget(_lbl("All Tests"), 3, 0)
        self.lbl_coef_all_y = _lbl("—")
        y_coef_grid.addWidget(self.lbl_coef_all_y, 3, 1)
        right_layout.addWidget(y_coef_box)

        # Z axis table (Slope)
        z_box, z_grid = _make_axis_table("Z", is_coef=False)
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
        left_layout.addWidget(z_box)

        # Z axis table (Coef)
        z_coef_box, z_coef_grid = _make_axis_table("Z", is_coef=True)
        z_coef_grid.addWidget(_lbl("45 lb"), 1, 0)
        self.lbl_coef_db_z = _lbl("—")
        z_coef_grid.addWidget(self.lbl_coef_db_z, 1, 1)
        z_coef_grid.addWidget(_lbl("Bodyweight"), 2, 0)
        self.lbl_coef_bw_z = _lbl("—")
        z_coef_grid.addWidget(self.lbl_coef_bw_z, 2, 1)
        z_coef_grid.addWidget(_lbl("All Tests"), 3, 0)
        self.lbl_coef_all_z = _lbl("—")
        z_coef_grid.addWidget(self.lbl_coef_all_z, 3, 1)
        right_layout.addWidget(z_coef_box)

        # Current-plot adjustment summary table (Slope)
        current_box = QtWidgets.QGroupBox("Current Plot (Slope)")
        cgrid = QtWidgets.QGridLayout(current_box)
        cgrid.setContentsMargins(6, 6, 6, 6)
        cgrid.setHorizontalSpacing(10)
        cgrid.setVerticalSpacing(4)
        
        # Toggles row
        self.chk_show_base = QtWidgets.QCheckBox("Show Base")
        self.chk_show_base.setChecked(True)
        self.chk_show_adj = QtWidgets.QCheckBox("Show Adj")
        self.chk_show_adj.setChecked(True)
        cgrid.addWidget(self.chk_show_base, 0, 0)
        cgrid.addWidget(self.chk_show_adj, 0, 1)

        cgrid.addWidget(_lbl("Base slope (solid line)"), 1, 0)
        self.lbl_plot_base_slope = _lbl("—")
        cgrid.addWidget(self.lbl_plot_base_slope, 1, 1)
        cgrid.addWidget(_lbl("Multiplier"), 2, 0)
        self.lbl_plot_multiplier = _lbl("—")
        cgrid.addWidget(self.lbl_plot_multiplier, 2, 1)
        cgrid.addWidget(_lbl("Adj slope (dashed line)"), 3, 0)
        self.lbl_plot_adj_slope = _lbl("—")
        cgrid.addWidget(self.lbl_plot_adj_slope, 3, 1)
        cgrid.addWidget(_lbl("% better vs base (SSE)"), 4, 0)
        self.lbl_plot_improve_pct = _lbl("—")
        cgrid.addWidget(self.lbl_plot_improve_pct, 4, 1)
        left_layout.addWidget(current_box)

        # Current-plot adjustment summary table (Coef)
        current_coef_box = QtWidgets.QGroupBox("Current Plot (Coef)")
        cc_grid = QtWidgets.QGridLayout(current_coef_box)
        cc_grid.setContentsMargins(6, 6, 6, 6)
        cc_grid.setHorizontalSpacing(10)
        cc_grid.setVerticalSpacing(4)
        
        # Toggles row
        self.chk_show_coef = QtWidgets.QCheckBox("Show Coef Line")
        self.chk_show_coef.setChecked(False) # Default off to avoid clutter initially? User didn't specify, but safer.
        cc_grid.addWidget(self.chk_show_coef, 0, 0, 1, 2)

        # Placeholder info rows
        cc_grid.addWidget(_lbl("Avg Coef"), 1, 0)
        self.lbl_plot_avg_coef = _lbl("—")
        cc_grid.addWidget(self.lbl_plot_avg_coef, 1, 1)

        right_layout.addWidget(current_coef_box)
        
        # Add stretch to both columns
        left_layout.addStretch(1)
        right_layout.addStretch(1)

        # Combine columns into a horizontal layout
        h_layout = QtWidgets.QHBoxLayout()
        h_layout.setSpacing(12)
        h_layout.addLayout(left_layout, 1)
        h_layout.addLayout(right_layout, 1)
        
        root.addLayout(h_layout)

        # Connect toggles
        self.chk_show_base.stateChanged.connect(lambda: self.toggles_changed.emit())
        self.chk_show_adj.stateChanged.connect(lambda: self.toggles_changed.emit())
        self.chk_show_coef.stateChanged.connect(lambda: self.toggles_changed.emit())

    # --- Public API ---------------------------------------------------------

    def get_toggles(self) -> dict:
        """Return state of plot line toggles."""
        return {
            "show_base": self.chk_show_base.isChecked(),
            "show_adj": self.chk_show_adj.isChecked(),
            "show_coef": self.chk_show_coef.isChecked(),
        }

    def set_slopes(
        self,
        avgs: Dict[str, Dict[str, float]],
        stds: Dict[str, Dict[str, float]],
        coeffs: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> None:
        """
        Update the slope/STD/Coef tables for X/Y/Z axes.

        avgs/stds/coeffs are expected in the format:
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

        def _get_coef(ph: str, ax: str) -> float:
            try:
                return float((coeffs or {}).get(ph, {}).get(ax, 0.0))
            except Exception:
                return 0.0

        try:
            self.lbl_slope_db_x.setText(f"{_get('45lb', 'x'):.6f}")
            self.lbl_std_db_x.setText(f"{_get_std('45lb', 'x'):.6f}")
            self.lbl_coef_db_x.setText(f"{_get_coef('45lb', 'x'):.6f}")

            self.lbl_slope_bw_x.setText(f"{_get('bodyweight', 'x'):.6f}")
            self.lbl_std_bw_x.setText(f"{_get_std('bodyweight', 'x'):.6f}")
            self.lbl_coef_bw_x.setText(f"{_get_coef('bodyweight', 'x'):.6f}")

            self.lbl_slope_all_x.setText(f"{_get('all', 'x'):.6f}")
            self.lbl_std_all_x.setText(f"{_get_std('all', 'x'):.6f}")
            self.lbl_coef_all_x.setText(f"{_get_coef('all', 'x'):.6f}")
        except Exception:
            pass

        try:
            self.lbl_slope_db_y.setText(f"{_get('45lb', 'y'):.6f}")
            self.lbl_std_db_y.setText(f"{_get_std('45lb', 'y'):.6f}")
            self.lbl_coef_db_y.setText(f"{_get_coef('45lb', 'y'):.6f}")

            self.lbl_slope_bw_y.setText(f"{_get('bodyweight', 'y'):.6f}")
            self.lbl_std_bw_y.setText(f"{_get_std('bodyweight', 'y'):.6f}")
            self.lbl_coef_bw_y.setText(f"{_get_coef('bodyweight', 'y'):.6f}")

            self.lbl_slope_all_y.setText(f"{_get('all', 'y'):.6f}")
            self.lbl_std_all_y.setText(f"{_get_std('all', 'y'):.6f}")
            self.lbl_coef_all_y.setText(f"{_get_coef('all', 'y'):.6f}")
        except Exception:
            pass

        try:
            self.lbl_slope_db_z.setText(f"{_get('45lb', 'z'):.6f}")
            self.lbl_std_db_z.setText(f"{_get_std('45lb', 'z'):.6f}")
            self.lbl_coef_db_z.setText(f"{_get_coef('45lb', 'z'):.6f}")

            self.lbl_slope_bw_z.setText(f"{_get('bodyweight', 'z'):.6f}")
            self.lbl_std_bw_z.setText(f"{_get_std('bodyweight', 'z'):.6f}")
            self.lbl_coef_bw_z.setText(f"{_get_coef('bodyweight', 'z'):.6f}")

            self.lbl_slope_all_z.setText(f"{_get('all', 'z'):.6f}")
            self.lbl_std_all_z.setText(f"{_get_std('all', 'z'):.6f}")
            self.lbl_coef_all_z.setText(f"{_get_coef('all', 'z'):.6f}")
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
            
        # Coef stats
        coef_val = m.get("coef_val")
        try:
            if coef_val is None:
                self.lbl_plot_avg_coef.setText("—")
            else:
                self.lbl_plot_avg_coef.setText(f"{float(coef_val):.6f}")
        except Exception:
            pass



