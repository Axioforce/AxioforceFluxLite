from __future__ import annotations

import time
from typing import Callable, Protocol


class _Canvas(Protocol):
    def set_snapshots(self, snapshots: dict) -> None: ...


class _SensorPlot(Protocol):
    def set_dual_series_enabled(self, enabled: bool) -> None: ...

    def add_point_launch(self, t_ms: int, fx: float, fy: float, fz: float) -> None: ...

    def add_point_landing(self, t_ms: int, fx: float, fy: float, fz: float) -> None: ...


class MoundRenderThrottler:
    """
    Buffer and render Pitching Mound Launch/Landing samples at a stable UI rate.

    The backend can emit mound packets at very high rates (~400-500Hz). We buffer the latest samples
    and only touch Qt widgets on a GUI-thread timer tick.
    """

    def __init__(self) -> None:
        self._latest: dict[str, dict] = {}
        self._last_rendered_ms: dict[str, int] = {"launch": 0, "landing": 0}

    def try_buffer_virtual_zone_frames(
        self,
        *,
        display_mode: str,
        mound_group_id: str,
        frames: list,
        cop_to_m: Callable[[object], float],
    ) -> bool:
        """
        Fast-path: if we're in mound mode and the mound group is active, buffer just the latest
        virtual-zone frames ("Pitching Mound.Launch Zone" / "Pitching Mound.Landing Zone") and return True.
        """
        if display_mode != "mound":
            return False
        if not mound_group_id:
            return False
        if not isinstance(frames, list) or not frames:
            return False

        try:
            for frame in frames:
                did = str((frame or {}).get("id") or (frame or {}).get("deviceId") or "").strip()
                if did not in ("Pitching Mound.Launch Zone", "Pitching Mound.Landing Zone"):
                    continue

                frame_group_id = str((frame or {}).get("groupId") or (frame or {}).get("group_id") or "").strip()
                if frame_group_id and frame_group_id != mound_group_id:
                    continue

                t_ms = int((frame or {}).get("time") or (frame or {}).get("t") or 0)
                if t_ms <= 0:
                    t_ms = int(time.time() * 1000)

                cop = (frame or {}).get("cop") or {}
                moments = (frame or {}).get("moments") or {}

                entry = {
                    "t_ms": int(t_ms),
                    "fx": float((frame or {}).get("fx", 0.0)),
                    "fy": float((frame or {}).get("fy", 0.0)),
                    "fz": float((frame or {}).get("fz", 0.0)),
                    "cop_x": float(cop_to_m(cop.get("x", 0.0))),
                    "cop_y": float(cop_to_m(cop.get("y", 0.0))),
                    "moments": {
                        "x": float(moments.get("x", 0.0)),
                        "y": float(moments.get("y", 0.0)),
                        "z": float(moments.get("z", 0.0)),
                    },
                    "group_id": frame_group_id,
                }

                if did.endswith("Launch Zone"):
                    self._latest["launch"] = entry
                else:
                    self._latest["landing"] = entry
            return True
        except Exception:
            # If buffering fails, caller should fall back to legacy per-packet rendering.
            return False

    def on_tick(
        self,
        *,
        display_mode: str,
        mound_group_id: str,
        canvas_left: _Canvas | None,
        canvas_right: _Canvas | None,
        sensor_plot_left: _SensorPlot | None,
        sensor_plot_right: _SensorPlot | None,
    ) -> None:
        """
        Render buffered Launch/Landing samples and update canvases/plots.
        Intended to be called from a GUI-thread QTimer.
        """
        try:
            if display_mode != "mound":
                return
            if not mound_group_id:
                return
            latest = self._latest
            if not latest:
                return

            # Ensure dual-series UI is enabled while in mound mode (idempotent).
            try:
                if sensor_plot_left:
                    sensor_plot_left.set_dual_series_enabled(True)
                if sensor_plot_right:
                    sensor_plot_right.set_dual_series_enabled(True)
            except Exception:
                pass

            snapshots: dict = {}

            # Launch zone
            l = latest.get("launch") if isinstance(latest.get("launch"), dict) else None
            if l:
                is_visible = abs(float(l.get("fz", 0.0))) > 5.0
                snap = (
                    float(l.get("cop_x", 0.0)),
                    float(l.get("cop_y", 0.0)),
                    float(l.get("fz", 0.0)),
                    int(l.get("t_ms", 0)),
                    bool(is_visible),
                    float(l.get("cop_x", 0.0)),
                    float(l.get("cop_y", 0.0)),
                )
                snapshots["Launch Zone"] = snap

                t_ms = int(l.get("t_ms", 0) or 0)
                if t_ms and t_ms != int(self._last_rendered_ms.get("launch", 0) or 0):
                    self._last_rendered_ms["launch"] = t_ms
                    fx, fy, fz = float(l.get("fx", 0.0)), float(l.get("fy", 0.0)), float(l.get("fz", 0.0))
                    if sensor_plot_left:
                        sensor_plot_left.add_point_launch(t_ms, fx, fy, fz)
                    if sensor_plot_right:
                        sensor_plot_right.add_point_launch(t_ms, fx, fy, fz)

            # Landing zone (virtual midpoint between the two 08 plates)
            r = latest.get("landing") if isinstance(latest.get("landing"), dict) else None
            if r:
                is_visible = abs(float(r.get("fz", 0.0))) > 5.0
                snap = (
                    float(r.get("cop_x", 0.0)),
                    float(r.get("cop_y", 0.0)),
                    float(r.get("fz", 0.0)),
                    int(r.get("t_ms", 0)),
                    bool(is_visible),
                    float(r.get("cop_x", 0.0)),
                    float(r.get("cop_y", 0.0)),
                )
                snapshots["Landing Zone"] = snap

                t_ms = int(r.get("t_ms", 0) or 0)
                if t_ms and t_ms != int(self._last_rendered_ms.get("landing", 0) or 0):
                    self._last_rendered_ms["landing"] = t_ms
                    fx, fy, fz = float(r.get("fx", 0.0)), float(r.get("fy", 0.0)), float(r.get("fz", 0.0))
                    if sensor_plot_left:
                        sensor_plot_left.add_point_landing(t_ms, fx, fy, fz)
                    if sensor_plot_right:
                        sensor_plot_right.add_point_landing(t_ms, fx, fy, fz)

            if snapshots:
                if canvas_left:
                    canvas_left.set_snapshots(snapshots)
                if canvas_right:
                    canvas_right.set_snapshots(snapshots)
        except Exception:
            return

