from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from .. import config


@dataclass
class ViewState:
    px_per_mm: float = config.PX_PER_MM
    cop_scale_k: float = config.COP_SCALE_K
    flags: config.UiFlags = field(default_factory=config.UiFlags)
    connection_text: str = "Disconnected"
    data_rate_text: str = "Hz: --"
    # Display configuration
    display_mode: str = "mound"  # "mound" or "single"
    selected_device_id: Optional[str] = None  # axfId / full device id
    selected_device_type: Optional[str] = None  # "06", "07", "08", or "11"
    selected_device_name: Optional[str] = None  # human-friendly name
    plate_device_ids: Dict[str, str] = field(default_factory=dict)
    mound_devices: Dict[str, Optional[str]] = field(default_factory=lambda: {
        "Launch Zone": None,
        "Upper Landing Zone": None,
        "Lower Landing Zone": None,
    })
    # Session defaults (persisted across sessions for convenience)
    last_tester_name: str = ""
    last_body_weight_n: float = 0.0


