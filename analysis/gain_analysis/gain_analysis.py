from __future__ import annotations

import argparse
import os
from typing import Dict, List, Optional

from .backend_runner import BackendConfig, process_csv_with_cache
from .compute_gain import (
    align_sumz_by_time,
    compute_gain_rows,
    parse_processed_sumz,
    summarize_gain,
    write_gain_rows_csv,
    write_summary_csv,
)
from .io_discrete import load_discrete_rows, iter_discrete_csv_paths


def _parse_coef_sweep(spec: str) -> List[float]:
    """
    Parse a coef sweep spec like:
      - "0.001:0.010:0.001" (start:stop:step, inclusive stop with epsilon)
      - "0.001,0.002,0.003"
    """
    s = (spec or "").strip()
    if ":" in s:
        parts = [p.strip() for p in s.split(":") if p.strip()]
        if len(parts) != 3:
            raise ValueError(f"bad sweep spec: {spec}")
        start, stop, step = (float(parts[0]), float(parts[1]), float(parts[2]))
        out: List[float] = []
        v = start
        # inclusive with small epsilon
        while v <= stop + 1e-12:
            out.append(round(v, 6))
            v += step
        return out
    return [float(p.strip()) for p in s.split(",") if p.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description="Gain analysis from discrete temp datasets.")
    ap.add_argument("--data-root", default="discrete_temp_testing", help="Root directory to crawl for discrete CSVs.")
    ap.add_argument("--host", default=None, help="Backend host (e.g. http://localhost). If omitted, uses SOCKET_HOST.")
    ap.add_argument("--port", type=int, default=None, help="Backend HTTP port. If omitted, uses HTTP_PORT.")
    ap.add_argument("--room-temp-f", type=float, default=76.0, help="Room temperature baseline (F).")
    ap.add_argument("--coef-sweep", default="0.001:0.010:0.001", help="Coef sweep spec, e.g. 0.001:0.010:0.001")
    ap.add_argument("--out-dir", default=os.path.join("analysis", "gain_analysis_output"), help="Output directory.")
    ap.add_argument("--cache-dir", default=None, help="Cache directory for processed CSVs (defaults under out-dir).")
    ap.add_argument("--min-abs-din", type=float, default=0.002, help="Min |din| to keep a gain row.")
    ap.add_argument("--timeout-s", type=int, default=300, help="Backend request timeout seconds.")
    ap.add_argument("--limit-files", type=int, default=0, help="Optional: limit number of source CSVs processed.")
    args = ap.parse_args()

    # Import app config lazily (so analysis tooling can run from repo root)
    from src import config as app_config  # type: ignore

    host = args.host or getattr(app_config, "SOCKET_HOST", "http://localhost")
    port = int(args.port or getattr(app_config, "HTTP_PORT", 3000))

    cfg = BackendConfig(host=host, port=port, room_temperature_f=float(args.room_temp_f))

    out_dir = os.path.abspath(args.out_dir)
    cache_dir = os.path.abspath(args.cache_dir or os.path.join(out_dir, "cache"))
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(cache_dir, exist_ok=True)

    coefs = _parse_coef_sweep(args.coef_sweep)

    all_gain_rows = []
    file_count = 0
    # IMPORTANT: iter_discrete_csv_paths() is session-only by design.
    # discrete_temp_measurements.csv is plot-only overlay data and must not be used here.
    for csv_path in iter_discrete_csv_paths(args.data_root):
        file_count += 1
        if args.limit_files and file_count > int(args.limit_files):
            break

        raw_rows = load_discrete_rows(csv_path)
        if not raw_rows:
            continue

        device_id = raw_rows[0].device_id

        # Baseline (no correction)
        processed_off = process_csv_with_cache(
            cfg=cfg,
            input_csv_path=csv_path,
            device_id=device_id,
            cache_dir=cache_dir,
            coef_z=None,
            timeout_s=int(args.timeout_s),
        )
        f0_pairs = parse_processed_sumz(processed_off)
        f0_list = align_sumz_by_time(raw_rows, f0_pairs)

        for c in coefs:
            processed_on = process_csv_with_cache(
                cfg=cfg,
                input_csv_path=csv_path,
                device_id=device_id,
                cache_dir=cache_dir,
                coef_z=float(c),
                timeout_s=int(args.timeout_s),
            )
            f1_pairs = parse_processed_sumz(processed_on)
            f1_list = align_sumz_by_time(raw_rows, f1_pairs)

            gain_rows = compute_gain_rows(
                raw_rows=raw_rows,
                f0_list=f0_list,
                f1_list=f1_list,
                coef_z=float(c),
                room_temp_f=float(args.room_temp_f),
                min_abs_din=float(args.min_abs_din),
            )
            all_gain_rows.extend(gain_rows)

    # Write outputs
    rows_csv = os.path.join(out_dir, "gain_rows.csv")
    write_gain_rows_csv(rows_csv, all_gain_rows)

    summary = summarize_gain(all_gain_rows)
    summary_csv = os.path.join(out_dir, "gain_summary.csv")
    write_summary_csv(summary_csv, summary)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


