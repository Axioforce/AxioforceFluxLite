from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from tools.AxioDash import tool_spec as axiodash_spec


@dataclass(frozen=True)
class ToolSpec:
    tool_id: str
    name: str
    kind: str  # "qt" | "web"
    description: str = ""
    url: Optional[str] = None


def default_tools() -> List[ToolSpec]:
    """
    FluxDeluxe tool registry (static for now).

    Later we can load these from:
    - a config file
    - entrypoints/plugins
    - a tools/ directory scan
    """
    return [
        ToolSpec(
            tool_id="fluxlite",
            name="FluxLite",
            kind="qt",
            description="Connect, visualize, and test plates/mounds.",
        ),
        ToolSpec(
            tool_id="metrics_editor",
            name="Metrics Editor",
            kind="qt",
            description="Compile/edit metrics truth store (Streamlit).",
        ),
        ToolSpec(
            tool_id=str(axiodash_spec.TOOL_ID),
            name=str(axiodash_spec.NAME),
            kind=str(axiodash_spec.KIND),
            description=str(axiodash_spec.DESCRIPTION),
            url=str(axiodash_spec.URL),
        ),
    ]

