from __future__ import annotations

import csv
import json
import os
import shutil
from typing import Dict, List, Tuple


def _repo_root() -> str:
    """Return project root (parent of src)."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _config_path() -> str:
    """Return path to JSON config used for data sync (stored under .aflite)."""
    root = _repo_root()
    meta_dir = os.path.join(root, ".aflite")
    try:
        os.makedirs(meta_dir, exist_ok=True)
    except Exception:
        pass
    return os.path.join(meta_dir, "data_sync.json")


def get_onedrive_data_root() -> str:
    """Load the OneDrive data root path from local config (per-machine)."""
    path = _config_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        return str(data.get("onedrive_data_root") or "").strip()
    except Exception:
        return ""


def set_onedrive_data_root(path_str: str) -> None:
    """Persist the OneDrive data root path to local config (per-machine)."""
    cfg_path = _config_path()
    data: Dict[str, object] = {}
    try:
        if os.path.isfile(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
    except Exception:
        data = {}
    data["onedrive_data_root"] = str(path_str or "").strip()
    try:
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def _collect_files(base_dir: str, exts: Tuple[str, ...]) -> Dict[str, str]:
    """
    Walk base_dir and return mapping of relative file paths -> absolute paths
    for files whose extension is in exts.

    Paths use forward slashes for portability.
    """
    out: Dict[str, str] = {}
    if not base_dir or not os.path.isdir(base_dir):
        return out
    exts_lower = tuple(e.lower() for e in exts)
    for root, _dirs, files in os.walk(base_dir):
        for name in files:
            if not any(name.lower().endswith(ext) for ext in exts_lower):
                continue
            abs_path = os.path.join(root, name)
            rel = os.path.relpath(abs_path, base_dir)
            rel_norm = rel.replace("\\", "/")
            out[rel_norm] = abs_path
    return out


def _collect_csvs(base_dir: str) -> Dict[str, str]:
    """Convenience wrapper to collect .csv files."""
    return _collect_files(base_dir, (".csv",))


def _merge_csv_two_way(local_path: str, remote_path: str) -> None:
    """
    Merge two CSV files by 'time' column (if present), writing the merged result back to both.

    - Creates a backup (.bak) of the local file before overwriting.
    - Fails safely: if merge produces empty/invalid result or throws exception,
      logs error and DOES NOT overwrite existing files.
    """

    def _load(path: str) -> Tuple[List[str], List[Dict[str, str]]]:
        try:
            if not os.path.isfile(path) or os.path.getsize(path) == 0:
                return [], []
            with open(path, "r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                fieldnames = list(reader.fieldnames or [])
                rows: List[Dict[str, str]] = []
                for row in reader:
                    if not row:
                        continue
                    rows.append({k: (v if v is not None else "") for k, v in row.items()})
            return fieldnames, rows
        except Exception:
            return [], []

    def _write(path: str, fieldnames: List[str], rows: List[Dict[str, str]]) -> None:
        # Atomic write via temp file
        temp_path = path + ".tmp"
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(temp_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for row in rows:
                    writer.writerow(row)
            # If write successful, rename over target
            if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                if os.path.exists(path):
                    # Create a backup of the original before overwriting
                    try:
                        shutil.copy2(path, path + ".bak")
                    except Exception:
                        pass
                shutil.move(temp_path, path)
        except Exception:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            raise

    try:
        lf, lrows = _load(local_path)
        rf, rrows = _load(remote_path)

        # If one side is completely empty/missing but the other has data,
        # treat as a simple copy (but verify the source is good).
        if not lf and not lrows and rf and rrows:
            _copy_newer(remote_path, local_path)
            return
        if not rf and not rrows and lf and lrows:
            _copy_newer(local_path, remote_path)
            return

        _write(local_path, fieldnames, merged)
        _write(remote_path, fieldnames, merged)

    except Exception as e:
        print(f"[sync] Merge failed for {local_path}: {e}")
        # Do NOT fall back to overwriting with _copy_newer on error
        return


def _copy_newer(path_a: str, path_b: str) -> None:
    """Copy the newer of the two files over the older, creating dirs as needed."""
    try:
        mtime_a = os.path.getmtime(path_a)
    except Exception:
        mtime_a = 0.0
    try:
        mtime_b = os.path.getmtime(path_b)
    except Exception:
        mtime_b = 0.0
    # Choose source: prefer existing, newer file
    if mtime_a >= mtime_b:
        src, dst = path_a, path_b
    else:
        src, dst = path_b, path_a
    try:
        if os.path.isfile(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
    except Exception:
        pass


def _merge_json_two_way(local_path: str, remote_path: str) -> None:
    """
    Merge two JSON metadata files (dict-like), writing the merged result back to both.

    Strategy:
        - Prefer the newer file (by mtime) as the base.
        - Overlay any missing keys from the older file.
        - If parsing fails or content is not a dict, fall back to copying newer over older.
    """

    def _load(path: str) -> Tuple[float, object]:
        try:
            mtime = os.path.getmtime(path)
        except Exception:
            mtime = 0.0
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return mtime, data

    try:
        mtime_l, data_l = _load(local_path)
        mtime_r, data_r = _load(remote_path)
        if not isinstance(data_l, dict) or not isinstance(data_r, dict):
            raise TypeError("metadata json is not a dict")
        # Choose base as newer file
        if mtime_l >= mtime_r:
            base = dict(data_l)
            other = data_r
        else:
            base = dict(data_r)
            other = data_l
        # Fill in any missing keys from the other side
        if isinstance(other, dict):
            for k, v in other.items():
                if k not in base:
                    base[k] = v
        payload = base
        # Write merged payload to both paths
        for path in (local_path, remote_path):
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2)
            except Exception:
                pass
    except Exception:
        # Fall back to newer-wins semantics
        _copy_newer(local_path, remote_path)


def _sync_tree(local_base: str, remote_base: str) -> None:
    """
    Sync CSV files between local_base and remote_base:

    - If file exists in both, merge by time and write back to both.
    - If file exists only on one side, copy it to the other side.
    """
    if not local_base or not remote_base:
        return
    local_map = _collect_csvs(local_base)
    remote_map = _collect_csvs(remote_base)
    keys = set(local_map) | set(remote_map)
    for rel in sorted(keys):
        local_path = local_map.get(rel)
        remote_path = remote_map.get(rel)
        # Both sides: merge
        if local_path and remote_path:
            _merge_csv_two_way(local_path, remote_path)
            continue
        # Local only -> copy to remote
        if local_path and not remote_path:
            remote_path = os.path.join(remote_base, rel.replace("/", os.sep))
            try:
                os.makedirs(os.path.dirname(remote_path), exist_ok=True)
                shutil.copy2(local_path, remote_path)
            except Exception:
                pass
            continue
        # Remote only -> copy to local
        if remote_path and not local_path:
            local_path = os.path.join(local_base, rel.replace("/", os.sep))
            try:
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                shutil.copy2(remote_path, local_path)
            except Exception:
                pass


def _sync_json_tree(local_base: str, remote_base: str) -> None:
    """
    Sync JSON metadata files between local_base and remote_base:

    - If file exists in both, merge dict fields and write back to both.
    - If file exists only on one side, copy it to the other side.
    """
    if not local_base or not remote_base:
        return
    local_map = _collect_files(local_base, (".json",))
    remote_map = _collect_files(remote_base, (".json",))
    keys = set(local_map) | set(remote_map)
    for rel in sorted(keys):
        local_path = local_map.get(rel)
        remote_path = remote_map.get(rel)
        if local_path and remote_path:
            _merge_json_two_way(local_path, remote_path)
            continue
        if local_path and not remote_path:
            remote_path = os.path.join(remote_base, rel.replace("/", os.sep))
            try:
                os.makedirs(os.path.dirname(remote_path), exist_ok=True)
                shutil.copy2(local_path, remote_path)
            except Exception:
                pass
            continue
        if remote_path and not local_path:
            local_path = os.path.join(local_base, rel.replace("/", os.sep))
            try:
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                shutil.copy2(remote_path, local_path)
            except Exception:
                pass


def sync_all_data(onedrive_root: str) -> None:
    """
    Sync CSV-based data between the local repo and a OneDrive-mirrored root.

    The following trees are mirrored under both roots:
        - discrete_temp_testing
        - temp_testing
        - live_test_logs
    """
    root = _repo_root()
    onedrive_root = str(onedrive_root or "").strip()
    if not onedrive_root:
        return
    # discrete_temp_testing
    local_disc = os.path.join(root, "discrete_temp_testing")
    remote_disc = os.path.join(onedrive_root, "discrete_temp_testing")
    _sync_tree(local_disc, remote_disc)
    _sync_json_tree(local_disc, remote_disc)
    # temp_testing
    local_temp = os.path.join(root, "temp_testing")
    remote_temp = os.path.join(onedrive_root, "temp_testing")
    _sync_tree(local_temp, remote_temp)
    _sync_json_tree(local_temp, remote_temp)
    # live_test_logs
    local_live = os.path.join(root, "live_test_logs")
    remote_live = os.path.join(onedrive_root, "live_test_logs")
    _sync_tree(local_live, remote_live)
    _sync_json_tree(local_live, remote_live)


