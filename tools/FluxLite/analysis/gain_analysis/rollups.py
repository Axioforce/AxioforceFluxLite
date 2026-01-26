from __future__ import annotations

import argparse
import csv
import math
import os
import statistics
from typing import Dict, Iterable, List, Optional, Tuple


def _safe_float(v: object, default: float = float("nan")) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


def _safe_int(v: object, default: int = 0) -> int:
    try:
        return int(float(v))
    except Exception:
        return int(default)


def _bucket_temp(t_f: float, bucket_f: float) -> str:
    if not bucket_f or bucket_f <= 0:
        return "na"
    lo = math.floor(float(t_f) / float(bucket_f)) * float(bucket_f)
    hi = lo + float(bucket_f)
    return f"{lo:.0f}-{hi:.0f}"


def _stats(vals: List[float]) -> Dict[str, float]:
    if not vals:
        return {
            "n": 0,
            "mean": float("nan"),
            "std": float("nan"),
            "median": float("nan"),
            "p25": float("nan"),
            "p75": float("nan"),
            "min": float("nan"),
            "max": float("nan"),
        }
    s = sorted(vals)
    n = len(s)
    mean = sum(s) / n
    std = statistics.pstdev(s) if n >= 2 else 0.0
    median = statistics.median(s)
    # quartiles (simple index-based)
    p25 = s[int(0.25 * (n - 1))]
    p75 = s[int(0.75 * (n - 1))]
    return {
        "n": n,
        "mean": float(mean),
        "std": float(std),
        "median": float(median),
        "p25": float(p25),
        "p75": float(p75),
        "min": float(min(s)),
        "max": float(max(s)),
    }


def load_gain_rows(path: str) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    with open(path, "r", encoding="utf-8", newline="") as handle:
        r = csv.DictReader(handle)
        for row in r:
            rows.append(dict(row))
    return rows


def write_csv(path: str, cols: List[str], rows: List[Dict[str, object]]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        w = csv.DictWriter(handle, fieldnames=cols)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in cols})


def main() -> int:
    ap = argparse.ArgumentParser(description="Rollups for gain_rows.csv")
    ap.add_argument(
        "--input",
        default=os.path.join("analysis", "gain_analysis_output", "gain_rows.csv"),
        help="Path to gain_rows.csv",
    )
    ap.add_argument(
        "--out-dir",
        default=os.path.join("analysis", "gain_analysis_output"),
        help="Directory to write rollup CSVs",
    )
    ap.add_argument(
        "--exclude-device-ids",
        default="07.0000004a",
        help="Comma-separated device_ids to exclude (e.g. known faulty plates).",
    )
    ap.add_argument(
        "--bucket-f",
        type=float,
        default=5.0,
        help="Temperature bucket size (F) for temp rollups.",
    )
    ap.add_argument(
        "--min-n",
        type=int,
        default=5,
        help="Minimum samples required for a group to be included.",
    )
    ap.add_argument(
        "--use-abs",
        action="store_true",
        help="Use abs(gain) instead of signed gain for rollups.",
    )
    ap.add_argument(
        "--by-coef",
        action="store_true",
        help="Include coef_z in grouping keys (otherwise aggregates across all coefs).",
    )
    ap.add_argument(
        "--coef-z",
        default="",
        help="Optional: restrict to specific coef_z values (comma-separated), e.g. 0.005 or 0.001,0.002,0.003",
    )
    args = ap.parse_args()

    in_path = os.path.abspath(args.input)
    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)

    exclude = {s.strip() for s in str(args.exclude_device_ids or "").split(",") if s.strip()}
    min_n = int(args.min_n)
    bucket_f = float(args.bucket_f)
    coef_filter = [float(s.strip()) for s in str(args.coef_z or "").split(",") if s.strip()]

    raw = load_gain_rows(in_path)

    # Filter + coerce numeric fields
    filtered: List[Dict[str, object]] = []
    for r in raw:
        dev = str(r.get("device_id") or "").strip()
        if dev in exclude:
            continue
        g = _safe_float(r.get("gain"))
        if math.isnan(g):
            continue
        if args.use_abs:
            g = abs(g)
        r2 = dict(r)
        r2["gain_val"] = float(g)
        r2["coef_z_f"] = _safe_float(r.get("coef_z"))
        if coef_filter:
            # match with a small tolerance (CSV float formatting varies)
            if not any(abs(float(r2["coef_z_f"]) - float(c)) <= 1e-9 for c in coef_filter):
                continue
        r2["sum_t_f_f"] = _safe_float(r.get("sum_t_f"))
        r2["plate_type"] = str(r.get("plate_type") or "").strip()
        r2["device_id"] = dev
        r2["phase"] = str(r.get("phase") or "").strip()
        r2["temp_bucket"] = _bucket_temp(r2["sum_t_f_f"], bucket_f=bucket_f)
        filtered.append(r2)

    # Group helpers
    def _key_plate_type(r: Dict[str, object]) -> Tuple:
        return (r["plate_type"], (r["coef_z_f"] if args.by_coef else None))

    def _key_plate(r: Dict[str, object]) -> Tuple:
        return (r["plate_type"], r["device_id"], (r["coef_z_f"] if args.by_coef else None))

    def _key_plate_type_temp(r: Dict[str, object]) -> Tuple:
        return (r["plate_type"], r["temp_bucket"], (r["coef_z_f"] if args.by_coef else None))

    def _key_plate_temp(r: Dict[str, object]) -> Tuple:
        return (r["plate_type"], r["device_id"], r["temp_bucket"], (r["coef_z_f"] if args.by_coef else None))

    def rollup(group_key_fn) -> List[Dict[str, object]]:
        groups: Dict[Tuple, List[float]] = {}
        for r in filtered:
            k = group_key_fn(r)
            groups.setdefault(k, []).append(float(r["gain_val"]))
        out_rows: List[Dict[str, object]] = []
        for k, vals in sorted(groups.items(), key=lambda kv: str(kv[0])):
            st = _stats(vals)
            if st["n"] < min_n:
                continue
            out_rows.append(
                {
                    "key": str(k),
                    "plate_type": k[0] if len(k) > 0 else "",
                    "device_id": (k[1] if len(k) > 1 and isinstance(k[1], str) else ""),
                    "temp_bucket_f": (k[1] if len(k) > 1 and isinstance(k[1], str) and "-" in k[1] else (k[2] if len(k) > 2 and isinstance(k[2], str) else "")),
                    "coef_z": (k[-1] if args.by_coef else ""),
                    "n": st["n"],
                    "gain_mean": st["mean"],
                    "gain_std": st["std"],
                    "gain_median": st["median"],
                    "gain_p25": st["p25"],
                    "gain_p75": st["p75"],
                    "gain_min": st["min"],
                    "gain_max": st["max"],
                }
            )
        return out_rows

    plate_type_rows = rollup(_key_plate_type)
    plate_rows = rollup(_key_plate)
    plate_type_temp_rows = rollup(_key_plate_type_temp)
    plate_temp_rows = rollup(_key_plate_temp)

    suffix = ("abs" if args.use_abs else "signed") + (f"_bycoef" if args.by_coef else "")
    if coef_filter:
        # Make filenames stable/readable: 0.005 -> coef0p005
        def _fmt(c: float) -> str:
            s = f"{c:.6f}".rstrip("0").rstrip(".")
            return "coef" + s.replace(".", "p")

        suffix += "_" + "_".join(_fmt(c) for c in coef_filter)

    cols = [
        "plate_type",
        "device_id",
        "temp_bucket_f",
        "coef_z",
        "n",
        "gain_mean",
        "gain_std",
        "gain_median",
        "gain_p25",
        "gain_p75",
        "gain_min",
        "gain_max",
        "key",
    ]

    write_csv(os.path.join(out_dir, f"rollup_plate_type_{suffix}.csv"), cols, plate_type_rows)
    write_csv(os.path.join(out_dir, f"rollup_plate_{suffix}.csv"), cols, plate_rows)
    write_csv(os.path.join(out_dir, f"rollup_plate_type_temp_{suffix}.csv"), cols, plate_type_temp_rows)
    write_csv(os.path.join(out_dir, f"rollup_plate_temp_{suffix}.csv"), cols, plate_temp_rows)

    print("Wrote rollups to", out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


