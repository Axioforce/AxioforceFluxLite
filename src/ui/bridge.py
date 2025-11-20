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
    force_vector_ready = QtCore.Signal(str, object, float, float, float)
    moments_ready = QtCore.Signal(object)  # Dict[str, Tuple[int, float, float, float]]
    mound_force_vectors_ready = QtCore.Signal(object)  # Dict[str, Tuple[int, float, float, float]] by zone
    dynamo_config_ready = QtCore.Signal(object)  # { 'samplingRate': int, 'emissionRate': int }
    # Raw backend payload (used for discrete temp detailed capture)
    raw_payload_ready = QtCore.Signal(object)  # Dict from backend JSON

    # Model management signals
    model_metadata_ready = QtCore.Signal(object)  # List[dict] | None
    model_package_status_ready = QtCore.Signal(object)  # StatusUpdate-like dict
    model_load_status_ready = QtCore.Signal(object)  # StatusUpdate-like dict
    model_activation_status_ready = QtCore.Signal(object)  # Activation/deactivation status


