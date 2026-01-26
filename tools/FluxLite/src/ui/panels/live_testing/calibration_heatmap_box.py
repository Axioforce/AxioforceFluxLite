from __future__ import annotations

from typing import Optional

from PySide6 import QtCore, QtWidgets


class CalibrationHeatmapBox(QtWidgets.QGroupBox):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__("Calibration Heatmap", parent)
        cal_layout = QtWidgets.QVBoxLayout(self)

        cal_row = QtWidgets.QHBoxLayout()
        cal_row.addWidget(QtWidgets.QLabel("Status:"))
        self.lbl_cal_status = QtWidgets.QLabel("—")
        cal_row.addWidget(self.lbl_cal_status)
        cal_row.addStretch(1)
        cal_layout.addLayout(cal_row)

        self.btn_load_45v = QtWidgets.QPushButton("Load Test Files…")
        self.btn_generate_heatmap = QtWidgets.QPushButton("Generate Heatmaps")
        self.btn_generate_heatmap.setEnabled(False)
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addWidget(self.btn_load_45v)
        btn_row.addWidget(self.btn_generate_heatmap)
        btn_row.addStretch(1)
        cal_layout.addLayout(btn_row)

        view_row = QtWidgets.QHBoxLayout()
        view_row.addWidget(QtWidgets.QLabel("View:"))
        self.heatmap_view_combo = QtWidgets.QComboBox()
        self.heatmap_view_combo.addItems(["Heatmap", "Grid View"])
        view_row.addWidget(self.heatmap_view_combo)
        view_row.addStretch(1)
        cal_layout.addLayout(view_row)

        # Metrics table
        self.metrics_table = QtWidgets.QTableWidget(5, 3)
        try:
            self.metrics_table.setHorizontalHeaderLabels(["Metric", "N", "%"])
            self.metrics_table.verticalHeader().setVisible(False)
            hh = self.metrics_table.horizontalHeader()
            vh = self.metrics_table.verticalHeader()
            try:
                hh.setStretchLastSection(False)
                from PySide6.QtWidgets import QHeaderView as _QHV

                hh.setSectionResizeMode(_QHV.ResizeToContents)
                vh.setSectionResizeMode(_QHV.ResizeToContents)
            except Exception:
                pass
            self.metrics_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            self.metrics_table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
            # Let the app-wide theme control gridlines + item styling.
            self.metrics_table.setShowGrid(True)
        except Exception:
            pass
        labels = ["Count", "Mean Error", "Median Error", "Max Error", "Bias (signed)"]
        for i, text in enumerate(labels):
            self.metrics_table.setItem(i, 0, QtWidgets.QTableWidgetItem(text))
            self.metrics_table.setItem(i, 1, QtWidgets.QTableWidgetItem("—"))
            self.metrics_table.setItem(i, 2, QtWidgets.QTableWidgetItem("—"))

        cal_layout.addWidget(QtWidgets.QLabel("Generated Heatmaps:"))
        self.heatmap_list = QtWidgets.QListWidget()
        try:
            self.heatmap_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        except Exception:
            pass

        hm_row = QtWidgets.QHBoxLayout()
        try:
            self.heatmap_list.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        except Exception:
            pass
        hm_row.addWidget(self.heatmap_list, 2)
        try:
            self.metrics_table.resizeColumnsToContents()
            self.metrics_table.resizeRowsToContents()
            self.metrics_table.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        except Exception:
            pass
        hm_row.addWidget(self.metrics_table, 0, QtCore.Qt.AlignTop)
        cal_layout.addLayout(hm_row, 1)

    def set_calibration_enabled(self, enabled: bool) -> None:
        try:
            self.btn_load_45v.setEnabled(bool(enabled))
        except Exception:
            pass

    def set_calibration_status(self, text: Optional[str]) -> None:
        try:
            self.lbl_cal_status.setText((text or "").strip() or "—")
        except Exception:
            pass

    def set_generate_enabled(self, enabled: bool) -> None:
        try:
            self.btn_generate_heatmap.setEnabled(bool(enabled))
        except Exception:
            pass

    def add_heatmap_entry(self, label: str, key: str, count: int) -> None:
        try:
            text = f"{label}  ({count})"
            item = QtWidgets.QListWidgetItem(text)
            item.setData(QtCore.Qt.UserRole, str(key))
            self.heatmap_list.addItem(item)
        except Exception:
            pass

    def clear_heatmap_entries(self) -> None:
        try:
            self.heatmap_list.clear()
        except Exception:
            pass

    def set_heatmap_metrics(self, metrics: dict, is_all: bool) -> None:
        try:
            count = int(metrics.get("count", 0))
            if not is_all:
                n_vals = [
                    str(count),
                    f"{float(metrics.get('mean_err', 0.0)):.1f}",
                    f"{float(metrics.get('median_err', 0.0)):.1f}",
                    f"{float(metrics.get('max_err', 0.0)):.1f}",
                    "—",
                ]
            else:
                n_vals = [str(count), "—", "—", "—", "—"]
            pct_vals = [
                "—",
                f"{float(metrics.get('mean_pct', 0.0)):.1f}",
                f"{float(metrics.get('median_pct', 0.0)):.1f}",
                f"{float(metrics.get('max_pct', 0.0)):.1f}",
                f"{float(metrics.get('signed_bias_pct', 0.0)):.1f}",
            ]
            for i, v in enumerate(n_vals):
                self.metrics_table.setItem(i, 1, QtWidgets.QTableWidgetItem(v))
            for i, v in enumerate(pct_vals):
                self.metrics_table.setItem(i, 2, QtWidgets.QTableWidgetItem(v))
        except Exception:
            pass

    def current_heatmap_view(self) -> str:
        try:
            return str(self.heatmap_view_combo.currentText() or "Heatmap")
        except Exception:
            return "Heatmap"


