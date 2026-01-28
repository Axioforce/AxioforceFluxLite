from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from typing import Deque, List, Optional, Tuple

from .geometry import GeometryService


@dataclass(frozen=True)
class LiveMeasurementConfig:
    # Arming: require >= this force continuously for arming_window_ms
    arming_min_fz_n: float = 50.0
    arming_window_ms: int = 1000

    # How long stability must be maintained to capture
    stability_duration_ms: int = 1000

    # Stability thresholds (checked continuously on sliding window)
    # Fz must stay within this percentage of mean force (e.g., 0.03 = 3%)
    stability_fz_range_pct: float = 0.03
    # COP must stay within this radius (max distance from centroid) AND same cell
    stability_cop_range_max_mm: float = 100.0

    # Median filter kernel size for noise reduction (must be odd)
    median_filter_size: int = 7


@dataclass(frozen=True)
class CaptureEvent:
    row: int
    col: int
    mean_fz_n: float
    mean_cop_x_mm: float
    mean_cop_y_mm: float
    std_fz_n: float
    window_ms: int
    # COP points used for the measurement (x_mm, y_mm) - for visualization
    cop_trail: Tuple[Tuple[float, float], ...]  = ()


def _median(values: List[float]) -> float:
    """Compute median of a list of values."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    mid = n // 2
    if n % 2 == 0:
        return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0
    return sorted_vals[mid]


def _apply_median_filter(values: List[float], kernel_size: int = 5) -> List[float]:
    """
    Apply a median filter to smooth noise while preserving edges/steps.

    Each output value is the median of the surrounding kernel_size values.
    """
    if not values or kernel_size < 1:
        return values

    # Ensure kernel size is odd
    if kernel_size % 2 == 0:
        kernel_size += 1

    half_k = kernel_size // 2
    n = len(values)
    result = []

    for i in range(n):
        start = max(0, i - half_k)
        end = min(n, i + half_k + 1)
        result.append(_median(values[start:end]))

    return result


class LiveMeasurementEngine:
    """
    Implements the live-testing arming -> stability -> capture state machine.

    Stability is evaluated continuously on a true sliding window:
    - Fz range (max - min) must be within threshold
    - COP range (max distance from centroid) must be within threshold
    - Both conditions must be met for the full window duration

    Noise filtering uses a median filter which preserves real step changes
    while removing impulse noise.

    Notes:
    - This engine is rotation-aware by taking a rotation_quadrants parameter and applying
      the same point rotation used by the renderer before mapping COP->cell.
    - It returns canonical (row, col) suitable for storing in the session.
    """

    def __init__(self, cfg: LiveMeasurementConfig | None = None) -> None:
        self.cfg = cfg or LiveMeasurementConfig()
        self.reset()

    def reset(self) -> None:
        self._arming_cell: Optional[Tuple[int, int]] = None
        self._arming_start_ms: Optional[int] = None
        self._active_cell: Optional[Tuple[int, int]] = None
        # (t_ms, cop_x_mm, cop_y_mm, fz_abs_n)
        self._window: Deque[Tuple[int, float, float, float]] = deque()
        self._last_status: str = ""
        # UI-friendly summary (keep it simple: idle|arming|measuring)
        self._phase: str = "idle"
        self._progress_01: float = 0.0
        # Track stability for progress indication
        self._stable_since_ms: Optional[int] = None

    @property
    def active_cell(self) -> Optional[Tuple[int, int]]:
        return self._active_cell

    @property
    def phase(self) -> str:
        """One of: idle|arming|measuring."""
        return self._phase

    @property
    def progress_01(self) -> float:
        """0..1 progress within the current phase (arming/measuring)."""
        try:
            return max(0.0, min(1.0, float(self._progress_01)))
        except Exception:
            return 0.0

    def status(self) -> str:
        return self._last_status

    def _check_stability(self) -> Tuple[bool, float, float, float, str]:
        """
        Check if the current window is stable.

        Returns: (is_stable, fz_range, fz_threshold, cop_range, reason)
        """
        if len(self._window) < 3:
            return False, 0.0, 0.0, 0.0, "collecting"

        # Extract raw values
        fz_values = [w[3] for w in self._window]
        cop_x_values = [w[1] for w in self._window]
        cop_y_values = [w[2] for w in self._window]

        # Apply median filter to Fz for noise reduction
        filtered_fz = _apply_median_filter(fz_values, self.cfg.median_filter_size)

        # Check Fz stability: range of filtered values as percentage of mean
        fz_min = min(filtered_fz)
        fz_max = max(filtered_fz)
        fz_range = fz_max - fz_min
        fz_mean = sum(filtered_fz) / len(filtered_fz)

        # Dynamic threshold: percentage of mean force (minimum 10N floor for low forces)
        fz_threshold = max(10.0, fz_mean * self.cfg.stability_fz_range_pct)

        # Check COP stability: max distance from centroid
        cop_x_mean = sum(cop_x_values) / len(cop_x_values)
        cop_y_mean = sum(cop_y_values) / len(cop_y_values)

        cop_range = 0.0
        for x, y in zip(cop_x_values, cop_y_values):
            dist = ((x - cop_x_mean) ** 2 + (y - cop_y_mean) ** 2) ** 0.5
            cop_range = max(cop_range, dist)

        # Build reason string for status
        fz_ok = fz_range <= fz_threshold
        cop_ok = cop_range <= self.cfg.stability_cop_range_max_mm

        if not fz_ok and not cop_ok:
            reason = f"Fz ±{fz_range:.1f}N (need ≤{fz_threshold:.1f}N), COP {cop_range:.0f}mm"
        elif not fz_ok:
            reason = f"Fz ±{fz_range:.1f}N (need ≤{fz_threshold:.1f}N @ {fz_mean:.0f}N)"
        elif not cop_ok:
            reason = f"COP {cop_range:.0f}mm (need ≤{self.cfg.stability_cop_range_max_mm:.0f}mm)"
        else:
            reason = "stable"

        return (fz_ok and cop_ok), fz_range, fz_threshold, cop_range, reason

    def process_sample(
        self,
        *,
        t_ms: int,
        cop_x_mm: float,
        cop_y_mm: float,
        fz_n: float,
        is_visible: bool,
        device_type: str,
        rows: int,
        cols: int,
        rotation_quadrants: int,
        is_cell_already_done,
    ) -> Optional[CaptureEvent]:
        """
        Process one sample.

        Parameters:
        - cop_x_mm/cop_y_mm are in physical coordinates (mm) before view rotation.
        - rotation_quadrants is the current UI rotation (0..3 clockwise).
        - is_cell_already_done: callable(row,col)->bool for current stage.
        """
        # Basic gating: no visible contact => reset
        if not is_visible:
            self._arming_cell = None
            self._arming_start_ms = None
            self._active_cell = None
            self._window.clear()
            self._stable_since_ms = None
            self._last_status = "Move load onto plate to arm…"
            self._phase = "idle"
            self._progress_01 = 0.0
            return None

        try:
            fz_abs = float(abs(fz_n))
        except Exception:
            fz_abs = 0.0

        # Rotate COP into the current view frame (matches rendered COP movement under rotation)
        rx_mm, ry_mm = GeometryService.apply_rotation(float(cop_x_mm), float(cop_y_mm), int(rotation_quadrants))

        # Map rotated COP to the on-screen grid cell
        cell_rc = GeometryService.map_cop_to_cell(
            device_type=str(device_type or "").strip(),
            rows=int(rows),
            cols=int(cols),
            x_mm=float(rx_mm),
            y_mm=float(ry_mm),
        )
        # Convert the on-screen cell into canonical session indices (invert rotation+device mapping).
        if cell_rc is not None:
            cell_rc = GeometryService.invert_map_cell(
                int(cell_rc[0]),
                int(cell_rc[1]),
                int(rows),
                int(cols),
                int(rotation_quadrants),
                str(device_type or "").strip(),
            )
        if cell_rc is None:
            # Off-plate: reset everything
            self._arming_cell = None
            self._arming_start_ms = None
            self._active_cell = None
            self._window.clear()
            self._stable_since_ms = None
            self._last_status = "Move load onto plate to arm…"
            self._phase = "idle"
            self._progress_01 = 0.0
            return None

        row, col = int(cell_rc[0]), int(cell_rc[1])

        # Always add to sliding window and trim old samples
        self._window.append((int(t_ms), float(cop_x_mm), float(cop_y_mm), float(fz_abs)))
        cutoff = int(t_ms) - int(self.cfg.stability_duration_ms)
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()

        # ===== ARMING PHASE =====
        if self._active_cell is None:
            # Skip already-done cells
            if is_cell_already_done(int(row), int(col)):
                self._arming_cell = None
                self._arming_start_ms = None
                self._window.clear()
                self._stable_since_ms = None
                self._last_status = "Already captured — move to next cell."
                self._phase = "idle"
                self._progress_01 = 0.0
                return None

            # Need minimum force to arm
            if fz_abs < float(self.cfg.arming_min_fz_n):
                self._arming_cell = None
                self._arming_start_ms = None
                self._last_status = f"Need ≥{self.cfg.arming_min_fz_n:.0f}N to arm"
                self._phase = "idle"
                self._progress_01 = 0.0
                return None

            # Must stay in same cell continuously during arming
            if self._arming_cell == (row, col):
                arm_span = int(t_ms) - int(self._arming_start_ms or int(t_ms))
            else:
                self._arming_cell = (row, col)
                self._arming_start_ms = int(t_ms)
                arm_span = 0

            self._phase = "arming"
            try:
                self._progress_01 = float(arm_span) / float(max(1, int(self.cfg.arming_window_ms)))
            except Exception:
                self._progress_01 = 0.0

            self._last_status = f"Arming… {arm_span}/{self.cfg.arming_window_ms}ms"

            if arm_span >= int(self.cfg.arming_window_ms):
                # Armed! Transition to measuring phase
                self._active_cell = (row, col)
                self._arming_cell = None
                self._arming_start_ms = None
                self._stable_since_ms = None
                self._last_status = "Armed — hold steady…"
                self._phase = "measuring"
                self._progress_01 = 0.0
            return None

        # ===== MEASURING PHASE =====
        # Must remain in the same cell
        if (row, col) != self._active_cell:
            self._active_cell = None
            self._window.clear()
            self._stable_since_ms = None
            self._last_status = f"Cell changed — need ≥{self.cfg.arming_min_fz_n:.0f}N to re-arm"
            self._phase = "idle"
            self._progress_01 = 0.0
            return None

        self._phase = "measuring"

        # Check if we have enough data in the window
        if len(self._window) < 3:
            self._last_status = "Collecting samples…"
            self._progress_01 = 0.0
            return None

        t_span = int(self._window[-1][0] - self._window[0][0])

        # Continuously check stability
        is_stable, fz_range, fz_threshold, cop_range, reason = self._check_stability()

        if is_stable:
            # Track how long we've been stable
            if self._stable_since_ms is None:
                self._stable_since_ms = int(t_ms)

            stable_duration = int(t_ms) - self._stable_since_ms

            # Progress based on how long we've been stable
            try:
                self._progress_01 = float(stable_duration) / float(max(1, int(self.cfg.stability_duration_ms)))
            except Exception:
                self._progress_01 = 0.0

            self._last_status = f"Stable… {stable_duration}/{self.cfg.stability_duration_ms}ms"

            # Check if we've been stable long enough to capture
            if stable_duration >= self.cfg.stability_duration_ms:
                # Compute final values using filtered data
                fz_values = [w[3] for w in self._window]
                filtered_fz = _apply_median_filter(fz_values, self.cfg.median_filter_size)

                mean_fz = sum(filtered_fz) / len(filtered_fz)
                mean_x = sum(w[1] for w in self._window) / len(self._window)
                mean_y = sum(w[2] for w in self._window) / len(self._window)

                # Compute std dev on filtered values for reporting
                if len(filtered_fz) > 1:
                    var = sum((v - mean_fz) ** 2 for v in filtered_fz) / (len(filtered_fz) - 1)
                    std_fz = var ** 0.5
                else:
                    std_fz = 0.0

                # Extract COP trail for visualization (subsample if too many points)
                cop_points = [(w[1], w[2]) for w in self._window]
                # Subsample to max ~50 points for rendering performance
                if len(cop_points) > 50:
                    step = len(cop_points) // 50
                    cop_points = cop_points[::step]
                cop_trail = tuple(cop_points)

                ev = CaptureEvent(
                    row=int(self._active_cell[0]),
                    col=int(self._active_cell[1]),
                    mean_fz_n=float(mean_fz),
                    mean_cop_x_mm=float(mean_x),
                    mean_cop_y_mm=float(mean_y),
                    std_fz_n=float(std_fz),
                    window_ms=int(stable_duration),
                    cop_trail=cop_trail,
                )

                # Reset for next cell
                self._active_cell = None
                self._window.clear()
                self._stable_since_ms = None
                self._last_status = "Captured! Move to next cell."
                self._phase = "idle"
                self._progress_01 = 0.0
                return ev
        else:
            # Not stable - reset stable tracking but keep sliding window
            self._stable_since_ms = None
            self._progress_01 = 0.0
            self._last_status = f"Hold steady… {reason}"

        return None
