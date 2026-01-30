from __future__ import annotations

from typing import Any


def extract_device_frames(payload: Any) -> list[dict]:
    """
    Normalize backend payload shapes into a list of device-frame dicts.

    Supported input shapes:
    - list[dict]: already a list of frames
    - dict with "devices": processed stream
    - dict with "sensors": raw stream; converted into a single "Sum" frame
    - dict representing a single frame (has "id" or "deviceId")
    """
    if isinstance(payload, list):
        return [f for f in payload if isinstance(f, dict)]

    if not isinstance(payload, dict):
        return []

    # Raw data stream: { deviceId, sensors:[...], cop:{...}, moments:{...} }
    if "sensors" in payload and isinstance(payload.get("sensors"), list):
        did = str(payload.get("deviceId") or "").strip()
        if not did:
            return []
        sum_sensor = next((s for s in payload["sensors"] if isinstance(s, dict) and s.get("name") == "Sum"), None)
        if not sum_sensor:
            return []

        cop_data = payload.get("cop") or {}
        moments_data_raw = payload.get("moments") or {}

        return [
            {
                "id": did,
                "fx": float(sum_sensor.get("x", 0.0)),
                "fy": float(sum_sensor.get("y", 0.0)),
                "fz": float(sum_sensor.get("z", 0.0)),
                "time": payload.get("time"),
                "avgTemperatureF": payload.get("avgTemperatureF"),
                "cop": {"x": float(getattr(cop_data, "get", lambda *_: 0.0)("x", 0.0)), "y": float(getattr(cop_data, "get", lambda *_: 0.0)("y", 0.0))},
                "moments": {
                    "x": float(getattr(moments_data_raw, "get", lambda *_: 0.0)("x", 0.0)),
                    "y": float(getattr(moments_data_raw, "get", lambda *_: 0.0)("y", 0.0)),
                    "z": float(getattr(moments_data_raw, "get", lambda *_: 0.0)("z", 0.0)),
                },
                "groupId": payload.get("groupId") or payload.get("group_id"),
            }
        ]

    # Processed stream: { devices:[...] }
    if "devices" in payload and isinstance(payload.get("devices"), list):
        return [f for f in payload["devices"] if isinstance(f, dict)]

    # Single frame dict
    if "id" in payload or "deviceId" in payload:
        return [payload]

    return []

