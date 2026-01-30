from __future__ import annotations

from typing import Dict, Iterable, List, Set, Tuple


def baseline_csvs_for_devices(
    *,
    repo,
    device_ids: Iterable[str],
    min_temp_f: float,
    max_temp_f: float,
) -> Set[str]:
    """
    Return set of raw_csv paths considered room-temp baselines for the given devices.
    """
    out: Set[str] = set()
    for dev in device_ids:
        try:
            entries = repo.list_temperature_room_baseline_tests(dev, min_temp_f=float(min_temp_f), max_temp_f=float(max_temp_f)) or []
            for e in entries:
                p = str((e or {}).get("csv_path") or "")
                if p:
                    out.add(p)
        except Exception:
            continue
    return out


def eligible_runs_by_device_and_temp(
    *,
    runs: List[dict],
    min_distinct_temps_per_device: int = 2,
) -> Tuple[int, List[dict], List[float]]:
    """
    Filter runs by requiring >=N distinct temps per device.
    Returns (eligible_devices, eligible_runs, all_temps)
    """
    by_dev: Dict[str, List[dict]] = {}
    for r in runs or []:
        dev = str((r or {}).get("device_id") or "")
        if not dev:
            continue
        by_dev.setdefault(dev, []).append(r)

    eligible_runs: List[dict] = []
    eligible_devices = 0
    all_temps: List[float] = []
    for dev, dev_runs in by_dev.items():
        temps = set()
        for rr in dev_runs:
            tf = (rr or {}).get("temp_f")
            if tf is None:
                continue
            try:
                temps.add(float(tf))
            except Exception:
                continue
        if len(temps) < int(min_distinct_temps_per_device):
            continue
        eligible_devices += 1
        all_temps.extend(list(temps))
        eligible_runs.extend(dev_runs)

    return eligible_devices, eligible_runs, all_temps

