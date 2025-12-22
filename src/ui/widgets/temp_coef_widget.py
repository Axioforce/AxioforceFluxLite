from __future__ import annotations

from typing import Dict, Optional

from PySide6 import QtCore, QtWidgets


class TempCoefWidget(QtWidgets.QWidget):
    """
    Discrete-temp coefficient metrics UI.

    This widget is purely view-level: it displays coefficient summaries computed elsewhere
    (TempPlotWidget + coef_math), and exposes a "Show Coef Line" toggle for the plot.
    """

    toggles_changed = QtCore.Signal()

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        def _lbl(text: str) -> QtWidgets.QLabel:
            lab = QtWidgets.QLabel(text)
            lab.setStyleSheet("color: rgb(220,220,230);")
            return lab

        # Toggle row
        toggle_box = QtWidgets.QGroupBox("Plot")
        tgrid = QtWidgets.QGridLayout(toggle_box)
        tgrid.setContentsMargins(6, 6, 6, 6)
        tgrid.setHorizontalSpacing(10)
        tgrid.setVerticalSpacing(4)
        self.chk_show_coef = QtWidgets.QCheckBox("Show Coef Line")
        self.chk_show_coef.setChecked(False)
        tgrid.addWidget(self.chk_show_coef, 0, 0, 1, 2)
        root.addWidget(toggle_box, 0)

        # Coef tables (Sum sensor, per axis)
        table_box = QtWidgets.QGroupBox("Coef (Sum sensor)")
        grid = QtWidgets.QGridLayout(table_box)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)

        grid.addWidget(_lbl("Phase"), 0, 0)
        grid.addWidget(_lbl("X"), 0, 1)
        grid.addWidget(_lbl("Y"), 0, 2)
        grid.addWidget(_lbl("Z"), 0, 3)

        def _row(r: int, label: str):
            grid.addWidget(_lbl(label), r, 0)
            lx = _lbl("—")
            ly = _lbl("—")
            lz = _lbl("—")
            grid.addWidget(lx, r, 1)
            grid.addWidget(ly, r, 2)
            grid.addWidget(lz, r, 3)
            return lx, ly, lz

        self._coef_45_x, self._coef_45_y, self._coef_45_z = _row(1, "45 lb")
        self._coef_bw_x, self._coef_bw_y, self._coef_bw_z = _row(2, "Bodyweight")
        self._coef_all_x, self._coef_all_y, self._coef_all_z = _row(3, "All (avg 45 lb + Bodyweight)")

        root.addWidget(table_box, 0)

        # Current selection details
        cur_box = QtWidgets.QGroupBox("Current Selection (from raw data)")
        cgrid = QtWidgets.QGridLayout(cur_box)
        cgrid.setContentsMargins(6, 6, 6, 6)
        cgrid.setHorizontalSpacing(12)
        cgrid.setVerticalSpacing(6)
        cgrid.addWidget(_lbl("Anchor T0 (°F)"), 0, 0)
        self.lbl_t0 = _lbl("—")
        cgrid.addWidget(self.lbl_t0, 0, 1)
        cgrid.addWidget(_lbl("Anchor Y0"), 1, 0)
        self.lbl_y0 = _lbl("—")
        cgrid.addWidget(self.lbl_y0, 1, 1)
        cgrid.addWidget(_lbl("Avg Coef"), 2, 0)
        self.lbl_coef = _lbl("—")
        cgrid.addWidget(self.lbl_coef, 2, 1)
        cgrid.addWidget(_lbl("N (coef samples)"), 3, 0)
        self.lbl_n = _lbl("—")
        cgrid.addWidget(self.lbl_n, 3, 1)
        root.addWidget(cur_box, 0)

        root.addStretch(1)

        self.chk_show_coef.stateChanged.connect(lambda: self.toggles_changed.emit())

    def get_toggles(self) -> dict:
        return {"show_coef": bool(self.chk_show_coef.isChecked())}

    def set_coef_table(self, coefs: Dict[str, Dict[str, float]]) -> None:
        """
        coefs format:
          {
            "45lb": {"x": float, "y": float, "z": float},
            "bodyweight": {...},
            "all": {...},
          }
        """

        def _get(ph: str, ax: str) -> str:
            try:
                return f"{float((coefs or {}).get(ph, {}).get(ax, 0.0)):.6f}"
            except Exception:
                return "—"

        try:
            self._coef_45_x.setText(_get("45lb", "x"))
            self._coef_45_y.setText(_get("45lb", "y"))
            self._coef_45_z.setText(_get("45lb", "z"))

            self._coef_bw_x.setText(_get("bodyweight", "x"))
            self._coef_bw_y.setText(_get("bodyweight", "y"))
            self._coef_bw_z.setText(_get("bodyweight", "z"))

            self._coef_all_x.setText(_get("all", "x"))
            self._coef_all_y.setText(_get("all", "y"))
            self._coef_all_z.setText(_get("all", "z"))
        except Exception:
            pass

    def set_current_selection_stats(self, stats: dict) -> None:
        """
        stats format:
          { "t0": float, "y0": float, "coef_mean": float, "n": int }
        """
        try:
            t0 = stats.get("t0")
            y0 = stats.get("y0")
            cm = stats.get("coef_mean")
            n = stats.get("n")
            self.lbl_t0.setText("—" if t0 is None else f"{float(t0):.2f}")
            self.lbl_y0.setText("—" if y0 is None else f"{float(y0):.3f}")
            self.lbl_coef.setText("—" if cm is None else f"{float(cm):.6f}")
            self.lbl_n.setText("—" if n is None else str(int(n)))
        except Exception:
            pass


