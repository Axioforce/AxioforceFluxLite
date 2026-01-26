from __future__ import annotations

from typing import Dict, Optional

from PySide6 import QtCore, QtWidgets, QtGui


class TempCoefWidget(QtWidgets.QWidget):
    """
    Discrete-temp coefficient metrics UI.

    This widget is purely view-level: it displays coefficient summaries computed elsewhere
    (TempPlotWidget + coef_math), and exposes a "Show Coef Line" toggle for the plot.
    """

    toggles_changed = QtCore.Signal()
    tuned_process_requested = QtCore.Signal()
    precise_tune_requested = QtCore.Signal()
    stop_tuning_requested = QtCore.Signal()
    generated_process_requested = QtCore.Signal()
    tuned_run_selected = QtCore.Signal(object)  # payload dict for a selected run

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        def _lbl(text: str) -> QtWidgets.QLabel:
            lab = QtWidgets.QLabel(text)
            lab.setStyleSheet("color: rgb(220,220,230);")
            return lab

        # Two-column layout: Normal (left) + Tuned (right)
        cols = QtWidgets.QHBoxLayout()
        cols.setContentsMargins(0, 0, 0, 0)
        cols.setSpacing(10)
        root.addLayout(cols, 1)

        # ----- Normal column -----
        left = QtWidgets.QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(8)
        cols.addLayout(left, 1)

        # Plot toggles (normal coef-line)
        toggle_box = QtWidgets.QGroupBox("Plot (Normal)")
        tgrid = QtWidgets.QGridLayout(toggle_box)
        tgrid.setContentsMargins(6, 6, 6, 6)
        tgrid.setHorizontalSpacing(10)
        tgrid.setVerticalSpacing(4)
        self.chk_show_coef = QtWidgets.QCheckBox("Show Coef Line (normal)")
        self.chk_show_coef.setChecked(False)
        tgrid.addWidget(self.chk_show_coef, 0, 0, 1, 2)
        left.addWidget(toggle_box, 0)

        # Coef tables (Sum sensor, per axis) - normal
        table_box = QtWidgets.QGroupBox("Coef (Sum sensor) — Normal")
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

        left.addWidget(table_box, 0)

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
        left.addWidget(cur_box, 0)
        # Process button for generated coefs (normal)
        self.btn_process_generated = QtWidgets.QPushButton("Process (Generated coefs → NN Corrected)")
        left.addWidget(self.btn_process_generated, 0)

        # ----- Tuned column -----
        right = QtWidgets.QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)
        cols.addLayout(right, 1)

        tuned_box = QtWidgets.QGroupBox("Tuned (search)")
        tuned_layout = QtWidgets.QVBoxLayout(tuned_box)
        tuned_layout.setContentsMargins(6, 6, 6, 6)
        tuned_layout.setSpacing(8)

        # Tuning controls
        ctrl = QtWidgets.QGroupBox("Tuning Controls")
        c = QtWidgets.QGridLayout(ctrl)
        c.setContentsMargins(6, 6, 6, 6)
        c.setHorizontalSpacing(10)
        c.setVerticalSpacing(6)
        c.addWidget(_lbl("Add runs:"), 0, 0)
        self.spin_runs = QtWidgets.QSpinBox()
        self.spin_runs.setRange(1, 500)
        self.spin_runs.setValue(50)
        c.addWidget(self.spin_runs, 0, 1)
        # Single tuned "process" button does tuning + best output generation
        self.btn_process_tuned = QtWidgets.QPushButton("Process Tuned (search + best)")
        c.addWidget(self.btn_process_tuned, 0, 2)
        self.btn_precise_tune = QtWidgets.QPushButton("Precise Tune")
        self.btn_precise_tune.setEnabled(False)
        c.addWidget(self.btn_precise_tune, 0, 3)
        self.btn_stop_tune = QtWidgets.QPushButton("Stop")
        self.btn_stop_tune.setEnabled(False)
        c.addWidget(self.btn_stop_tune, 0, 4)
        self.lbl_tune_status = _lbl("—")
        self.lbl_tune_status.setWordWrap(True)
        self.tune_progress = QtWidgets.QProgressBar()
        self.tune_progress.setRange(0, 100)
        self.tune_progress.setValue(0)
        try:
            self.tune_progress.setTextVisible(False)
        except Exception:
            pass
        c.addWidget(self.tune_progress, 1, 0, 1, 5)
        c.addWidget(self.lbl_tune_status, 2, 0, 1, 5)
        tuned_layout.addWidget(ctrl, 0)

        # Leaderboard (top runs)
        lb_box = QtWidgets.QGroupBox("Leaderboard (Top 10)")
        lb_layout = QtWidgets.QVBoxLayout(lb_box)
        lb_layout.setContentsMargins(6, 6, 6, 6)
        lb_layout.setSpacing(6)
        self.tbl_leaderboard = QtWidgets.QTableWidget(0, 5)
        self.tbl_leaderboard.setHorizontalHeaderLabels(["Score", "X", "Y", "Z", "Mode"])
        try:
            self.tbl_leaderboard.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
            self.tbl_leaderboard.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            self.tbl_leaderboard.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            self.tbl_leaderboard.verticalHeader().setVisible(False)
            self.tbl_leaderboard.horizontalHeader().setStretchLastSection(True)
        except Exception:
            pass
        try:
            lb_box.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
            self.tbl_leaderboard.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        except Exception:
            pass
        self.tbl_leaderboard.setMinimumHeight(140)
        lb_layout.addWidget(self.tbl_leaderboard, 1)
        tuned_layout.addWidget(lb_box, 1)
        right.addWidget(tuned_box, 1)

        root.addStretch(0)

        self.chk_show_coef.stateChanged.connect(lambda: self.toggles_changed.emit())
        self.btn_process_tuned.clicked.connect(lambda: self.tuned_process_requested.emit())
        self.btn_precise_tune.clicked.connect(lambda: self.precise_tune_requested.emit())
        self.btn_stop_tune.clicked.connect(lambda: self.stop_tuning_requested.emit())
        self.btn_process_generated.clicked.connect(lambda: self.generated_process_requested.emit())
        self.tbl_leaderboard.cellClicked.connect(self._on_leaderboard_clicked)

        # Internal enable state
        self._tuning_running = False
        self._has_best_run = False
        self._leaderboard_rows: list[dict] = []

    def _apply_tuning_button_state(self) -> None:
        try:
            can_start = not bool(self._tuning_running)
            self.spin_runs.setEnabled(can_start)
            self.btn_process_tuned.setEnabled(can_start)
            self.btn_precise_tune.setEnabled(bool(can_start and self._has_best_run))
            self.btn_stop_tune.setEnabled(bool(self._tuning_running))
        except Exception:
            pass

    def _on_leaderboard_clicked(self, row: int, _col: int) -> None:
        try:
            r = int(row)
        except Exception:
            return
        if r < 0 or r >= len(self._leaderboard_rows):
            return
        payload = self._leaderboard_rows[r]
        try:
            self.tuned_run_selected.emit(dict(payload or {}))
        except Exception:
            try:
                self.tuned_run_selected.emit(payload)
            except Exception:
                pass

    def get_toggles(self) -> dict:
        return {"show_coef": bool(self.chk_show_coef.isChecked())}

    def get_tune_runs(self) -> int:
        try:
            return int(self.spin_runs.value())
        except Exception:
            return 50

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

    def set_best_tuned_coefs(self, coefs: Optional[Dict[str, float]]) -> None:
        def _f(v: object) -> str:
            try:
                return f"{float(v):.6f}"
            except Exception:
                return "—"

        c = dict(coefs or {})
        # We no longer show a dedicated "best coefs" table; the leaderboard is the source of truth.
        # Keep this for compatibility with existing callers (it still enables Precise Tune once any best exists).
        self._has_best_run = bool(c) or bool(self._has_best_run)
        self._apply_tuning_button_state()

    def set_tuning_status(self, text: str) -> None:
        try:
            self.lbl_tune_status.setText((text or "").strip() or "—")
        except Exception:
            pass

    def set_tuning_progress(self, pairs_done: int, pairs_total: int) -> None:
        try:
            d = max(0, int(pairs_done))
            t = max(1, int(pairs_total))
            pct = int(round(100.0 * float(d) / float(t)))
            pct = max(0, min(100, pct))
            self.tune_progress.setValue(pct)
        except Exception:
            pass

    def set_tuning_enabled(self, enabled: bool) -> None:
        # enabled=True means user may start a tuning run (i.e. not currently running).
        self._tuning_running = not bool(enabled)
        self._apply_tuning_button_state()

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

    def set_tuning_leaderboard(self, rows: list[dict], *, select_first: bool = False) -> None:
        """
        rows format (expected):
          [{ "score_total": float, "coeffs": {"x":..,"y":..,"z":..}, "output_csv": str, "tuning_mode": str }, ...]
        """
        try:
            self._leaderboard_rows = list(rows or [])
        except Exception:
            self._leaderboard_rows = []
        self._has_best_run = bool(self._leaderboard_rows)
        self._apply_tuning_button_state()
        try:
            self.tbl_leaderboard.setRowCount(len(self._leaderboard_rows))
        except Exception:
            return

        def _it(v: object) -> QtWidgets.QTableWidgetItem:
            it = QtWidgets.QTableWidgetItem(str(v))
            try:
                it.setForeground(QtGui.QColor(220, 220, 230))
            except Exception:
                pass
            return it

        for i, row in enumerate(self._leaderboard_rows):
            try:
                score = float((row or {}).get("score_total") or float("inf"))
            except Exception:
                score = float("inf")
            coeffs = (row or {}).get("coeffs") or {}
            try:
                x = float((coeffs or {}).get("x") or 0.0)
                y = float((coeffs or {}).get("y") or 0.0)
                z = float((coeffs or {}).get("z") or 0.0)
            except Exception:
                x, y, z = 0.0, 0.0, 0.0
            mode = str((row or {}).get("tuning_mode") or "")
            self.tbl_leaderboard.setItem(i, 0, _it(f"{score:.6g}"))
            self.tbl_leaderboard.setItem(i, 1, _it(f"{x:.6f}"))
            self.tbl_leaderboard.setItem(i, 2, _it(f"{y:.6f}"))
            self.tbl_leaderboard.setItem(i, 3, _it(f"{z:.6f}"))
            self.tbl_leaderboard.setItem(i, 4, _it(mode))

        if select_first and self._leaderboard_rows:
            try:
                self.tbl_leaderboard.selectRow(0)
            except Exception:
                pass


