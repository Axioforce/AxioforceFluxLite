from __future__ import annotations

from typing import Optional

from PySide6 import QtWidgets


class TempsInTestBox(QtWidgets.QGroupBox):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__("Temps in Test", parent)
        layout = QtWidgets.QVBoxLayout(self)

        temps_header = QtWidgets.QHBoxLayout()
        self.lbl_temps_baseline = QtWidgets.QLabel("Includes Baseline:")
        self.lbl_temps_baseline_icon = QtWidgets.QLabel("✖")
        temps_header.addWidget(self.lbl_temps_baseline)
        temps_header.addWidget(self.lbl_temps_baseline_icon)
        temps_header.addStretch(1)
        layout.addLayout(temps_header)

        self.temps_list = QtWidgets.QListWidget()
        layout.addWidget(self.temps_list, 1)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(6)
        self.btn_plot_test = QtWidgets.QPushButton("Plot Test")
        self.btn_plot_test.setEnabled(False)
        self.btn_process_test = QtWidgets.QPushButton("Process")
        self.btn_process_test.setEnabled(False)
        btn_row.addWidget(self.btn_plot_test, 1)
        btn_row.addWidget(self.btn_process_test, 1)
        layout.addLayout(btn_row)

    def set_temps_in_test(self, includes_baseline: bool | None, temps_f: list[float]) -> None:
        """Update the Temps in Test tab with baseline indicator and temperature list."""
        try:
            if includes_baseline is None:
                self.lbl_temps_baseline_icon.setText("")
                self.lbl_temps_baseline_icon.setStyleSheet("")
            elif includes_baseline:
                self.lbl_temps_baseline_icon.setText("✔")
                self.lbl_temps_baseline_icon.setStyleSheet("color: #3CB371;")
            else:
                self.lbl_temps_baseline_icon.setText("✖")
                self.lbl_temps_baseline_icon.setStyleSheet("color: #CC4444;")
        except Exception:
            pass

        try:
            has_data = includes_baseline is True or bool(temps_f)
            self.btn_plot_test.setEnabled(bool(has_data))
            self.btn_process_test.setEnabled(bool(has_data))
        except Exception:
            self.btn_plot_test.setEnabled(False)
            try:
                self.btn_process_test.setEnabled(False)
            except Exception:
                pass

        try:
            self.temps_list.clear()
            for t in temps_f or []:
                try:
                    label = f"{float(t):.1f} °F"
                except Exception:
                    label = str(t)
                self.temps_list.addItem(label)
        except Exception:
            pass


