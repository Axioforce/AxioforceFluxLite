from __future__ import annotations

from typing import Optional

from PySide6 import QtCore, QtWidgets


class LiveCellDetailsPanel(QtWidgets.QGroupBox):
    """
    Reusable cell-inspector panel for live testing modes.

    For normal live testing, it shows:
    - measured value (N)
    - target (N)
    - signed percent error
    - Reset Cell button (clears the cell for the given stage)
    """

    reset_requested = QtCore.Signal(int, int, int)  # stage_idx, row, col
    reset_all_fail_requested = QtCore.Signal(int)  # stage_idx

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__("Cell Details", parent)
        self._stage_idx: Optional[int] = None
        self._row: Optional[int] = None
        self._col: Optional[int] = None

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignLeft)
        form.setFormAlignment(QtCore.Qt.AlignTop)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)

        self.lbl_cell = QtWidgets.QLabel("—")
        self.lbl_measured = QtWidgets.QLabel("—")
        self.lbl_target = QtWidgets.QLabel("—")
        self.lbl_err = QtWidgets.QLabel("—")

        form.addRow("Cell:", self.lbl_cell)
        form.addRow("Measured:", self.lbl_measured)
        form.addRow("Target:", self.lbl_target)
        form.addRow("Signed error:", self.lbl_err)

        root.addLayout(form)

        self.btn_reset = QtWidgets.QPushButton("Reset cell")
        try:
            self.btn_reset.setCursor(QtCore.Qt.PointingHandCursor)
        except Exception:
            pass
        self.btn_reset.clicked.connect(self._emit_reset)

        self.btn_reset_all_fail = QtWidgets.QPushButton("Reset all fail")
        try:
            self.btn_reset_all_fail.setCursor(QtCore.Qt.PointingHandCursor)
        except Exception:
            pass
        self.btn_reset_all_fail.clicked.connect(self._emit_reset_all_fail)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_reset)
        btn_row.addWidget(self.btn_reset_all_fail)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

        root.addStretch(1)
        self.clear()

    def clear(self) -> None:
        self._stage_idx = None
        self._row = None
        self._col = None
        self.lbl_cell.setText("—")
        self.lbl_measured.setText("—")
        self.lbl_target.setText("—")
        self.lbl_err.setText("—")
        try:
            self.btn_reset.setEnabled(False)
        except Exception:
            pass
        try:
            self.btn_reset_all_fail.setEnabled(False)
        except Exception:
            pass

    def set_cell(
        self,
        *,
        stage_idx: int,
        row: int,
        col: int,
        measured_n: Optional[float],
        target_n: Optional[float],
    ) -> None:
        self._stage_idx = int(stage_idx)
        self._row = int(row)
        self._col = int(col)

        self.lbl_cell.setText(f"{int(row) + 1},{int(col) + 1}")
        if measured_n is None:
            self.lbl_measured.setText("—")
        else:
            self.lbl_measured.setText(f"{float(measured_n):.1f} N")

        if target_n is None:
            self.lbl_target.setText("—")
            self.lbl_err.setText("—")
        else:
            self.lbl_target.setText(f"{float(target_n):.1f} N")
            if measured_n is None or abs(float(target_n)) < 1e-9:
                self.lbl_err.setText("—")
            else:
                signed_pct = (float(measured_n) - float(target_n)) / float(target_n) * 100.0
                self.lbl_err.setText(f"{signed_pct:+.1f}%")

        try:
            self.btn_reset.setEnabled(bool(measured_n is not None))
        except Exception:
            pass
        try:
            self.btn_reset_all_fail.setEnabled(True)
        except Exception:
            pass

    def _emit_reset(self) -> None:
        if self._stage_idx is None or self._row is None or self._col is None:
            return
        try:
            self.reset_requested.emit(int(self._stage_idx), int(self._row), int(self._col))
        except Exception:
            pass

    def _emit_reset_all_fail(self) -> None:
        if self._stage_idx is None:
            return
        try:
            self.reset_all_fail_requested.emit(int(self._stage_idx))
        except Exception:
            pass

