from __future__ import annotations

from PySide6 import QtCore


class UiBridge(QtCore.QObject):
    """Thread-safe bridge: controller threads emit signals; UI updates happen on the main thread."""

    snapshots_ready = QtCore.Signal(object, object)  # snaps: Dict[str, tuple], hz_text: Optional[str]
    connection_text_ready = QtCore.Signal(str)
    single_snapshot_ready = QtCore.Signal(object)  # Optional[tuple]
    plate_device_id_ready = QtCore.Signal(str, str)  # plate_name, device_id
    available_devices_ready = QtCore.Signal(object)  # List[Tuple[str, str, str]]
    active_devices_ready = QtCore.Signal(object)  # set[str]
    force_vector_ready = QtCore.Signal(str, int, float, float, float)
    moments_ready = QtCore.Signal(object)  # Dict[str, Tuple[int, float, float, float]]
    mound_force_vectors_ready = QtCore.Signal(object)  # Dict[str, Tuple[int, float, float, float]] by zone


