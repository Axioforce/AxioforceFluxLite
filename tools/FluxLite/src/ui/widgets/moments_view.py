from __future__ import annotations

from typing import Dict, Tuple

from PySide6 import QtCore, QtWidgets


class MomentsTable(QtWidgets.QTableWidget):
    rows_reordered = QtCore.Signal()

    def dropEvent(self, event: QtCore.QEvent) -> None:  # type: ignore[override]
        super().dropEvent(event)
        try:
            self.rows_reordered.emit()
        except Exception:
            pass


class MomentsViewWidget(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.table = MomentsTable(0, 4, self)
        self.table.setHorizontalHeaderLabels(["Device", "Mx", "My", "Mz"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        # Enable drag-and-drop row reordering
        self.table.setDragEnabled(True)
        self.table.setAcceptDrops(True)
        self.table.setDropIndicatorShown(True)
        self.table.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        try:
            self.table.setDefaultDropAction(QtCore.Qt.MoveAction)
        except Exception:
            pass
        layout.addWidget(self.table)

        self._device_row_map: Dict[str, int] = {}
        try:
            self.table.rows_reordered.connect(self._rebuild_row_map_from_table)
        except Exception:
            pass

    def _friendly_name(self, device_id: str) -> str:
        token = (device_id or "").strip()
        if not token:
            return ""
        if "." in token:
            return token.split(".")[-1]
        return token

    def _rebuild_row_map_from_table(self) -> None:
        mapping: Dict[str, int] = {}
        try:
            rows = self.table.rowCount()
            for r in range(rows):
                item = self.table.item(r, 0)
                if item is None:
                    continue
                dev_id = item.data(QtCore.Qt.UserRole)
                if isinstance(dev_id, str) and dev_id:
                    mapping[dev_id] = r
        except Exception:
            pass
        if mapping:
            self._device_row_map = mapping

    @QtCore.Slot(object)
    def set_moments(self, moments: Dict[str, Tuple[int, float, float, float]]) -> None:
        # Keep map in sync with any user reordering
        self._rebuild_row_map_from_table()
        # Update or insert rows for each device
        for device_id, (t_ms, mx, my, mz) in moments.items():
            if device_id not in self._device_row_map:
                row = self.table.rowCount()
                self.table.insertRow(row)
                self._device_row_map[device_id] = row
                name_item = QtWidgets.QTableWidgetItem(self._friendly_name(device_id))
                name_item.setData(QtCore.Qt.UserRole, device_id)
                self.table.setItem(row, 0, name_item)
                for col in range(1, 4):
                    self.table.setItem(row, col, QtWidgets.QTableWidgetItem(""))
            row = self._device_row_map[device_id]
            # Format with reasonable precision
            self.table.item(row, 1).setText(f"{mx:.6f}")
            self.table.item(row, 2).setText(f"{my:.6f}")
            self.table.item(row, 3).setText(f"{mz:.6f}")

        # Optionally remove rows for devices no longer in the snapshot
        # (keep for now to preserve history during transient dropouts)
        # Rebuild map once more in case rows were added
        self._rebuild_row_map_from_table()


