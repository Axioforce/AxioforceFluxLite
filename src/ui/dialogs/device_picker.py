from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6 import QtCore, QtWidgets


class DevicePickerDialog(QtWidgets.QDialog):
    """Dialog for selecting a device for a mound position."""

    def __init__(self, position_name: str, device_type: str, available_devices: List[Tuple[str, str, str]], parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.selected_device: Optional[Tuple[str, str, str]] = None

        self.setWindowTitle(f"Select Device for {position_name}")
        self.setModal(True)
        self.setMinimumWidth(400)
        self.setMinimumHeight(300)

        layout = QtWidgets.QVBoxLayout(self)

        label = QtWidgets.QLabel(f"Select a Type {device_type} device for {position_name}:")
        layout.addWidget(label)

        self.device_list = QtWidgets.QListWidget()
        self.device_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        for name, axf_id, dev_type in available_devices:
            if dev_type == device_type:
                display = f"{name} ({axf_id})"
                item = QtWidgets.QListWidgetItem(display)
                item.setData(QtCore.Qt.UserRole, (name, axf_id, dev_type))
                self.device_list.addItem(item)
        layout.addWidget(self.device_list)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.device_list.itemDoubleClicked.connect(self._on_accept)

    def _on_accept(self) -> None:
        current = self.device_list.currentItem()
        if current:
            self.selected_device = current.data(QtCore.Qt.UserRole)
            self.accept()


