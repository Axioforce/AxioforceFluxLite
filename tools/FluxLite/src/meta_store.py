from __future__ import annotations

import os
import sqlite3
from typing import Optional, Tuple, Any

_DB_PATH: Optional[str] = None


def _repo_root() -> str:
    # src/meta_store.py -> repo root is parent of src
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _db_file_path() -> str:
    global _DB_PATH
    if _DB_PATH:
        return _DB_PATH
    root = _repo_root()
    meta_dir = os.path.join(root, ".aflite")
    try:
        os.makedirs(meta_dir, exist_ok=True)
    except Exception:
        pass
    _DB_PATH = os.path.join(meta_dir, "meta.db")
    return _DB_PATH


def init_db() -> None:
    path = _db_file_path()
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS live_session_meta (
                device_id TEXT NOT NULL,
                model_id TEXT,
                tester TEXT,
                body_weight_n REAL,
                capture_name TEXT,
                csv_dir TEXT,
                started_at_ms INTEGER NOT NULL,
                PRIMARY KEY (device_id, started_at_ms)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_runs (
                csv_path TEXT NOT NULL,
                device_id TEXT NOT NULL,
                slope_x REAL NOT NULL DEFAULT 0.0,
                slope_y REAL NOT NULL DEFAULT 0.0,
                slope_z REAL NOT NULL DEFAULT 0.0,
                output_on TEXT,
                output_off TEXT,
                processed_at_ms INTEGER NOT NULL,
                UNIQUE (csv_path, slope_x, slope_y, slope_z)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS stage_marks (
                device_id TEXT NOT NULL,
                capture_name TEXT NOT NULL,
                stage_name TEXT NOT NULL,
                idx INTEGER NOT NULL,
                start_ms INTEGER,
                end_ms INTEGER,
                session_started_at_ms INTEGER,
                PRIMARY KEY (device_id, capture_name, idx, start_ms)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def insert_live_session_meta(
    device_id: str,
    model_id: Optional[str],
    tester: Optional[str],
    body_weight_n: Optional[float],
    capture_name: Optional[str],
    csv_dir: Optional[str],
    started_at_ms: int,
) -> None:
    if not (device_id or "").strip():
        return
    path = _db_file_path()
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO live_session_meta
            (device_id, model_id, tester, body_weight_n, capture_name, csv_dir, started_at_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(device_id).strip(),
                (model_id or "").strip(),
                (tester or "").strip(),
                float(body_weight_n) if body_weight_n is not None else None,
                (capture_name or "").strip(),
                (csv_dir or "").strip(),
                int(started_at_ms),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_body_weight(device_id: str) -> Optional[float]:
    """Return the most recent stored body weight for this device_id, or None."""
    if not (device_id or "").strip():
        return None
    path = _db_file_path()
    if not os.path.isfile(path):
        return None
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT body_weight_n
            FROM live_session_meta
            WHERE device_id = ?
            ORDER BY started_at_ms DESC
            LIMIT 1
            """,
            (str(device_id).strip(),),
        )
        row = cur.fetchone()
        if not row:
            return None
        try:
            bw = row[0]
            return float(bw) if bw is not None else None
        except Exception:
            return None
    finally:
        conn.close()

def start_stage_mark(
    device_id: str,
    capture_name: str,
    stage_name: str,
    idx: int,
    start_ms: int,
    session_started_at_ms: int | None = None,
) -> None:
    if not (device_id or "").strip() or not (capture_name or "").strip():
        return
    path = _db_file_path()
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO stage_marks (device_id, capture_name, stage_name, idx, start_ms, end_ms, session_started_at_ms)
            VALUES (?, ?, ?, ?, ?, NULL, ?)
            """,
            (str(device_id).strip(), str(capture_name).strip(), str(stage_name).strip(), int(idx), int(start_ms), None if session_started_at_ms is None else int(session_started_at_ms)),
        )
        conn.commit()
    finally:
        conn.close()

def end_stage_mark(
    device_id: str,
    capture_name: str,
    idx: int,
    end_ms: int,
) -> None:
    if not (device_id or "").strip() or not (capture_name or "").strip():
        return
    path = _db_file_path()
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE stage_marks
            SET end_ms = ?
            WHERE device_id = ? AND capture_name = ? AND idx = ?
              AND end_ms IS NULL
            """,
            (int(end_ms), str(device_id).strip(), str(capture_name).strip(), int(idx)),
        )
        conn.commit()
    finally:
        conn.close()

def get_stage_marks(device_id: str, capture_name: str) -> list[dict]:
    if not (device_id or "").strip() or not (capture_name or "").strip():
        return []
    path = _db_file_path()
    if not os.path.isfile(path):
        return []
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT stage_name, idx, start_ms, end_ms, session_started_at_ms
            FROM stage_marks
            WHERE device_id = ? AND capture_name = ?
            ORDER BY idx ASC, start_ms ASC
            """,
            (str(device_id).strip(), str(capture_name).strip()),
        )
        rows = cur.fetchall() or []
        out: list[dict] = []
        for r in rows:
            out.append(
                {
                    "stage_name": r[0],
                    "idx": r[1],
                    "start_ms": r[2],
                    "end_ms": r[3],
                    "session_started_at_ms": r[4],
                }
            )
        return out
    finally:
        conn.close()

def upsert_processed_run(
    csv_path: str,
    device_id: str,
    slope_x: float | None,
    slope_y: float | None,
    slope_z: float | None,
    output_on: str | None,
    output_off: str | None,
    processed_at_ms: int,
) -> None:
    path = _db_file_path()
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        # Normalize None slopes to 0.0 to align with UNIQUE constraint
        sx = 0.0 if slope_x is None else float(slope_x)
        sy = 0.0 if slope_y is None else float(slope_y)
        sz = 0.0 if slope_z is None else float(slope_z)
        cur.execute(
            """
            INSERT INTO processed_runs (csv_path, device_id, slope_x, slope_y, slope_z, output_on, output_off, processed_at_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(csv_path, slope_x, slope_y, slope_z)
            DO UPDATE SET
                output_on=excluded.output_on,
                output_off=excluded.output_off,
                processed_at_ms=excluded.processed_at_ms
            """,
            (
                str(csv_path or "").strip(),
                str(device_id or "").strip(),
                float(sx),
                float(sy),
                float(sz),
                (output_on or "").strip() or None,
                (output_off or "").strip() or None,
                int(processed_at_ms),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def has_off_for_csv(csv_path: str) -> bool:
    path = _db_file_path()
    if not os.path.isfile(path):
        return False
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT 1 FROM processed_runs
            WHERE csv_path = ? AND output_off IS NOT NULL
            LIMIT 1
            """,
            (str(csv_path or "").strip(),),
        )
        return cur.fetchone() is not None
    finally:
        conn.close()


def has_on_for_csv(csv_path: str, sx: float, sy: float, sz: float) -> bool:
    path = _db_file_path()
    if not os.path.isfile(path):
        return False
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT 1 FROM processed_runs
            WHERE csv_path = ? AND
                  slope_x = ? AND
                  slope_y = ? AND
                  slope_z = ? AND
                  output_on IS NOT NULL
            LIMIT 1
            """,
            (str(csv_path or "").strip(), float(sx or 0.0), float(sy or 0.0), float(sz or 0.0)),
        )
        return cur.fetchone() is not None
    finally:
        conn.close()


def get_runs_for_csv(csv_path: str) -> list[dict]:
    path = _db_file_path()
    if not os.path.isfile(path):
        return []
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT slope_x, slope_y, slope_z, output_on, output_off, processed_at_ms
            FROM processed_runs
            WHERE csv_path = ?
            ORDER BY processed_at_ms DESC
            """,
            (str(csv_path or "").strip(),),
        )
        rows = cur.fetchall() or []
        out: list[dict] = []
        for r in rows:
            out.append(
                {
                    "slope_x": r[0],
                    "slope_y": r[1],
                    "slope_z": r[2],
                    "output_on": r[3],
                    "output_off": r[4],
                    "processed_at_ms": r[5],
                }
            )
        return out
    finally:
        conn.close()

