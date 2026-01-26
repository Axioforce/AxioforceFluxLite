from __future__ import annotations

import json
import os
from typing import Optional

from PySide6 import QtWidgets


class SessionInfoBox(QtWidgets.QGroupBox):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__("Session Info", parent)
        meta_layout = QtWidgets.QFormLayout(self)

        # Editable fields for tester and body weight
        self.edit_tester = QtWidgets.QLineEdit()
        self.edit_tester.setPlaceholderText("Enter tester name")
        self.lbl_device = QtWidgets.QLabel("—")
        self.lbl_model = QtWidgets.QLabel("—")
        self.spin_bw = QtWidgets.QDoubleSpinBox()
        self.spin_bw.setRange(0.0, 5000.0)
        self.spin_bw.setDecimals(1)
        self.spin_bw.setSuffix(" N")
        self.spin_bw.setSingleStep(1.0)
        self.spin_bw.setSpecialValueText("—")  # Show "—" when value is 0

        meta_layout.addRow("Tester:", self.edit_tester)
        meta_layout.addRow("Device ID:", self.lbl_device)
        meta_layout.addRow("Model ID:", self.lbl_model)
        meta_layout.addRow("Body Weight:", self.spin_bw)

        # Discrete temp test metadata (shown only in discrete mode)
        self.lbl_test_date_title = QtWidgets.QLabel("Test Date:")
        self.lbl_test_date = QtWidgets.QLabel("—")
        meta_layout.addRow(self.lbl_test_date_title, self.lbl_test_date)
        self.lbl_short_label_title = QtWidgets.QLabel("Short Label:")
        self.lbl_short_label = QtWidgets.QLabel("—")
        meta_layout.addRow(self.lbl_short_label_title, self.lbl_short_label)

        # Threshold labels (no heading, just the values)
        self.lbl_thresh_db = QtWidgets.QLabel("—")
        self.lbl_thresh_bw = QtWidgets.QLabel("—")
        meta_layout.addRow("45 lb DB (±N):", self.lbl_thresh_db)
        meta_layout.addRow("Body Weight (±N):", self.lbl_thresh_bw)

        # Keep backward compatibility aliases
        self.lbl_tester = self.edit_tester  # For code that reads .text()
        self.lbl_bw = self.spin_bw  # For code that reads value

    def get_tester_name(self) -> str:
        """Get the current tester name from the editable field."""
        return self.edit_tester.text().strip()

    def get_body_weight_n(self) -> float:
        """Get the current body weight from the spin box."""
        return float(self.spin_bw.value())

    def set_tester_name(self, name: str) -> None:
        """Set the tester name in the editable field."""
        self.edit_tester.setText(name or "")

    def set_body_weight_n(self, weight_n: float) -> None:
        """Set the body weight in the spin box."""
        try:
            self.spin_bw.setValue(float(weight_n) if weight_n else 0.0)
        except Exception:
            self.spin_bw.setValue(0.0)

    def apply_discrete_test_meta(self, key: str) -> None:
        """
        For discrete temp tests, load test_meta.json in the selected folder and show it
        in the Session Info pane.
        """
        # Clear defaults
        try:
            self.edit_tester.setText("")
            self.lbl_device.setText("—")
            self.lbl_model.setText("—")
            self.spin_bw.setValue(0.0)
            self.lbl_test_date.setText("—")
            self.lbl_short_label.setText("—")
        except Exception:
            pass

        if not key:
            return

        base = str(key)
        try:
            if os.path.isfile(base):
                base = os.path.dirname(base)
        except Exception:
            pass

        meta_path = os.path.join(base, "test_meta.json")
        if not os.path.isfile(meta_path):
            return

        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f) or {}
        except Exception:
            meta = {}

        if not isinstance(meta, dict):
            return

        try:
            self.edit_tester.setText(str(meta.get("tester_name") or meta.get("tester") or "").strip())
        except Exception:
            pass
        try:
            self.lbl_device.setText(str(meta.get("device_id") or meta.get("deviceId") or "").strip() or "—")
        except Exception:
            pass
        try:
            self.lbl_model.setText(str(meta.get("model_id") or meta.get("modelId") or "").strip() or "—")
        except Exception:
            pass
        try:
            bw = meta.get("body_weight_n")
            self.spin_bw.setValue(float(bw) if bw is not None else 0.0)
        except Exception:
            pass
        try:
            self.lbl_test_date.setText(str(meta.get("date") or "").strip() or "—")
        except Exception:
            pass
        try:
            self.lbl_short_label.setText(str(meta.get("short_label") or "").strip() or "—")
        except Exception:
            pass
