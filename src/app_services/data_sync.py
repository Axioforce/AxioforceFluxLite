from __future__ import annotations
from PySide6 import QtCore
from ..core import sync_logic as data_sync

class DataSyncService(QtCore.QObject):
    """
    Service for synchronizing data with OneDrive.
    Wraps the existing functional implementation in src/data_sync.py.
    """
    sync_started = QtCore.Signal()
    sync_finished = QtCore.Signal(bool, str) # success, message

    def __init__(self):
        super().__init__()

    def get_onedrive_root(self) -> str:
        return data_sync.get_onedrive_data_root()

    def set_onedrive_root(self, path: str) -> None:
        data_sync.set_onedrive_data_root(path)

    def sync_all(self, onedrive_root: str) -> None:
        self.sync_started.emit()
        try:
            data_sync.sync_all_data(onedrive_root)
            self.sync_finished.emit(True, "Data synchronized successfully.")
        except Exception as e:
            self.sync_finished.emit(False, str(e))
