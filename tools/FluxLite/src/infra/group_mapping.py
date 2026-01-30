from __future__ import annotations

from typing import Dict

from .backend_address import BackendAddress
from .http_client import get_json


def detect_existing_mound_mapping(addr: BackendAddress, *, timeout_s: float = 4.0) -> Dict[str, str]:
    """
    Detect an existing 'pitching mound' configuration via backend groups API.

    Returns a mapping of:
      - "Launch Zone" -> device_id
      - "Upper Landing Zone" -> device_id
      - "Lower Landing Zone" -> device_id
    """
    url = addr.get_groups_url()
    payload = get_json(url, timeout_s=float(timeout_s))

    groups = payload.get("response") or payload.get("groups") or []
    mapping: Dict[str, str] = {}

    for g in groups:
        try:
            cfg = str(g.get("group_configuration") or g.get("configuration") or "").lower()
        except Exception:
            cfg = ""

        if "pitching" not in cfg or "mound" not in cfg:
            continue

        for d in (g.get("devices") or []):
            try:
                device_id = str(d.get("axf_id") or d.get("deviceId") or "").strip()
                pos_id = str(d.get("position_id") or d.get("positionId") or d.get("name") or d.get("plateName") or "").strip()
                is_virtual = bool(d.get("is_virtual"))
            except Exception:
                continue

            if not device_id or not pos_id or is_virtual:
                continue

            if pos_id in ("Upper Landing Zone", "Lower Landing Zone"):
                mapping[pos_id] = device_id
            elif pos_id == "Launch Zone":
                mapping["Launch Zone"] = device_id

        break  # Found the relevant group; stop searching.

    return mapping


