from __future__ import annotations

from typing import Optional


def parse_coef_key(coef_key: str) -> Optional[dict]:
    """
    Parse stored coef_key strings like:
      "scalar:x=0.002000,y=0.002000,z=0.002000"

    Returns:
      { "mode": str, "x": float, "y": float, "z": float }
    or None if parse fails.
    """
    s = str(coef_key or "").strip()
    if not s:
        return None
    try:
        mode = ""
        rest = s
        if ":" in s:
            mode, rest = s.split(":", 1)
        parts = {}
        for p in rest.split(","):
            p = p.strip()
            if "=" not in p:
                continue
            k, v = p.split("=", 1)
            parts[k.strip().lower()] = float(v)
        if not all(k in parts for k in ("x", "y", "z")):
            return None
        return {"mode": str(mode or "").strip().lower(), "x": float(parts["x"]), "y": float(parts["y"]), "z": float(parts["z"])}
    except Exception:
        return None


