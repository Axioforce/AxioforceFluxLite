from __future__ import annotations

import os
from typing import Optional

from PySide6 import QtCore, QtWidgets

from ...delegates import DiscreteTestDelegate
from ....project_paths import data_dir


class SessionControlsBox(QtWidgets.QGroupBox):
    """
    Session Controls group box for `LiveTestingPanel`.

    Exposes child widgets as attributes so the parent panel can bind to them
    for backwards-compatible method implementations.
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__("Session Controls", parent)
        controls_layout = QtWidgets.QVBoxLayout(self)

        # Backing store for discrete tests (for filtering)
        self._all_discrete_tests: list[tuple[str, str, str]] = []

        # Session type selector (Normal vs Temperature Test vs Discrete Temp.)
        mode_row = QtWidgets.QHBoxLayout()
        mode_row.addWidget(QtWidgets.QLabel("Session Type:"))
        self.session_mode_combo = QtWidgets.QComboBox()
        try:
            self.session_mode_combo.addItems(["Normal", "Temperature Test", "Discrete Temp. Testing"])
        except Exception:
            pass
        mode_row.addWidget(self.session_mode_combo)
        mode_row.addStretch(1)
        controls_layout.addLayout(mode_row)

        # Discrete temp testing test picker
        discrete_picker_box = QtWidgets.QVBoxLayout()
        self.lbl_discrete_tests = QtWidgets.QLabel("Tests:")
        self.lbl_discrete_tests.setVisible(False)

        filters_row = QtWidgets.QHBoxLayout()
        filters_row.setContentsMargins(0, 0, 0, 0)
        filters_row.setSpacing(6)
        self.discrete_type_filter = QtWidgets.QComboBox()
        self.discrete_type_filter.addItems(["All types", "06", "07", "08", "11"])
        self.discrete_plate_filter = QtWidgets.QComboBox()
        self.discrete_plate_filter.addItem("All plates")
        self.discrete_type_label = QtWidgets.QLabel("Type:")
        self.discrete_plate_label = QtWidgets.QLabel("Plate:")
        filters_row.addWidget(self.discrete_type_label)
        filters_row.addWidget(self.discrete_type_filter)
        filters_row.addWidget(self.discrete_plate_label)
        filters_row.addWidget(self.discrete_plate_filter, 1)
        discrete_picker_box.addLayout(filters_row)

        self.discrete_test_list = QtWidgets.QListWidget()
        try:
            self.discrete_test_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            self.discrete_test_list.setUniformItemSizes(True)
            self.discrete_test_list.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
            self.discrete_test_list.setItemDelegate(DiscreteTestDelegate(self.discrete_test_list))
        except Exception:
            pass
        discrete_picker_box.addWidget(self.discrete_test_list, 1)
        controls_layout.addLayout(discrete_picker_box)

        # Discrete temp actions
        discrete_row = QtWidgets.QHBoxLayout()
        self.btn_discrete_new = QtWidgets.QPushButton("Start New Test")
        self.btn_discrete_add = QtWidgets.QPushButton("Add to Existing Test")
        self.btn_discrete_add.setEnabled(False)
        try:
            self.btn_discrete_new.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            self.btn_discrete_add.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        except Exception:
            pass
        discrete_row.addWidget(self.btn_discrete_new, 1)
        discrete_row.addWidget(self.btn_discrete_add, 1)
        controls_layout.addLayout(discrete_row)

        # Standard session controls
        self.btn_start = QtWidgets.QPushButton("Start Session")
        self.btn_end = QtWidgets.QPushButton("End Session")
        self.btn_end.setEnabled(False)
        self.btn_next = QtWidgets.QPushButton("Next Stage")
        self.btn_next.setEnabled(False)

        nav_row = QtWidgets.QHBoxLayout()
        self.btn_prev = QtWidgets.QPushButton("Previous Stage")
        nav_row.addWidget(self.btn_prev)
        nav_row.addWidget(self.btn_next)
        nav_row.addStretch(1)

        stage_row = QtWidgets.QHBoxLayout()
        self.lbl_stage_title = QtWidgets.QLabel("Stage:")
        stage_row.addWidget(self.lbl_stage_title)
        self.stage_label = QtWidgets.QLabel("—")
        stage_row.addWidget(self.stage_label)
        stage_row.addStretch(1)

        progress_row = QtWidgets.QHBoxLayout()
        self.lbl_progress_title = QtWidgets.QLabel("Progress:")
        progress_row.addWidget(self.lbl_progress_title)
        self.progress_label = QtWidgets.QLabel("0 / 0 cells")
        progress_row.addWidget(self.progress_label)
        progress_row.addStretch(1)

        controls_layout.addWidget(self.btn_start)
        controls_layout.addWidget(self.btn_end)
        controls_layout.addLayout(nav_row)
        controls_layout.addLayout(stage_row)
        controls_layout.addLayout(progress_row)

    def set_discrete_tests(self, tests: list[tuple[str, str, str]]) -> None:
        """Populate discrete test picker with (label, date, key) triples."""
        self._all_discrete_tests = list(tests or [])

        # Refresh plate filter options based on available device ids
        try:
            device_ids: set[str] = set()
            base_dir = data_dir("discrete_temp_testing")
            for _label, _date_str, key in self._all_discrete_tests:
                path = str(key)
                try:
                    rel = os.path.relpath(path, base_dir)
                except Exception:
                    rel = path
                parts = rel.split(os.sep)
                if parts and parts[0]:
                    device_ids.add(parts[0])
            self.discrete_plate_filter.blockSignals(True)
            self.discrete_plate_filter.clear()
            self.discrete_plate_filter.addItem("All plates")
            for did in sorted(device_ids):
                self.discrete_plate_filter.addItem(did)
        except Exception:
            pass
        finally:
            try:
                self.discrete_plate_filter.blockSignals(False)
            except Exception:
                pass

        self.apply_discrete_filters()

    def apply_discrete_filters(self) -> None:
        """Re-populate discrete_test_list based on current filter selections."""
        try:
            self.discrete_test_list.blockSignals(True)
        except Exception:
            pass
        try:
            self.discrete_test_list.clear()
            base_dir = data_dir("discrete_temp_testing")
            try:
                type_sel = str(self.discrete_type_filter.currentText() or "All types")
            except Exception:
                type_sel = "All types"
            try:
                plate_sel = str(self.discrete_plate_filter.currentText() or "All plates")
            except Exception:
                plate_sel = "All plates"

            for label, date_str, key in self._all_discrete_tests:
                path = str(key)
                try:
                    rel = os.path.relpath(path, base_dir)
                except Exception:
                    rel = path
                parts = rel.split(os.sep)
                device_id = parts[0] if parts else ""
                dev_type = ""
                if device_id:
                    if "." in device_id:
                        dev_type = device_id.split(".", 1)[0]
                    else:
                        dev_type = device_id[:2]

                if type_sel != "All types" and dev_type != type_sel:
                    continue
                if plate_sel != "All plates" and device_id != plate_sel:
                    continue

                item = QtWidgets.QListWidgetItem()
                try:
                    item.setData(QtCore.Qt.UserRole, path)
                    item.setData(QtCore.Qt.UserRole + 1, str(label))
                    item.setData(QtCore.Qt.UserRole + 2, str(date_str))
                    item.setData(QtCore.Qt.UserRole + 3, device_id)
                except Exception:
                    pass
                self.discrete_test_list.addItem(item)
        except Exception:
            pass
        finally:
            try:
                self.discrete_test_list.blockSignals(False)
            except Exception:
                pass

    def current_discrete_test_key(self) -> str:
        try:
            item = self.discrete_test_list.currentItem()
            if item is None:
                return ""
            key = item.data(QtCore.Qt.UserRole)
            return str(key or "")
        except Exception:
            return ""


