from __future__ import annotations

import json
import os
from typing import Optional

from PySide6 import QtWidgets


class SessionInfoBox(QtWidgets.QGroupBox):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__("Session Info & Thresholds", parent)
        meta_layout = QtWidgets.QFormLayout(self)
        self.lbl_tester = QtWidgets.QLabel("—")
        self.lbl_device = QtWidgets.QLabel("—")
        self.lbl_model = QtWidgets.QLabel("—")
        self.lbl_bw = QtWidgets.QLabel("—")
        meta_layout.addRow("Tester:", self.lbl_tester)
        meta_layout.addRow("Device ID:", self.lbl_device)
        meta_layout.addRow("Model ID:", self.lbl_model)
        meta_layout.addRow("Body Weight (N):", self.lbl_bw)

        # Discrete temp test metadata (shown only in discrete mode)
        self.lbl_test_date_title = QtWidgets.QLabel("Test Date:")
        self.lbl_test_date = QtWidgets.QLabel("—")
        meta_layout.addRow(self.lbl_test_date_title, self.lbl_test_date)
        self.lbl_short_label_title = QtWidgets.QLabel("Short Label:")
        self.lbl_short_label = QtWidgets.QLabel("—")
        meta_layout.addRow(self.lbl_short_label_title, self.lbl_short_label)

        self.lbl_thresh_db = QtWidgets.QLabel("—")
        self.lbl_thresh_bw = QtWidgets.QLabel("—")
        meta_layout.addRow("45 lb DB (±N):", self.lbl_thresh_db)
        meta_layout.addRow("Body Weight (±N):", self.lbl_thresh_bw)

    def apply_discrete_test_meta(self, key: str) -> None:
        """
        For discrete temp tests, load test_meta.json in the selected folder and show it
        in the Session Info pane.
        """
        # Clear defaults
        try:
            self.lbl_tester.setText("—")
            self.lbl_device.setText("—")
            self.lbl_model.setText("—")
            self.lbl_bw.setText("—")
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
            self.lbl_tester.setText(str(meta.get("tester_name") or meta.get("tester") or "").strip() or "—")
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
            self.lbl_bw.setText("—" if bw is None else f"{float(bw):.1f}")
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


