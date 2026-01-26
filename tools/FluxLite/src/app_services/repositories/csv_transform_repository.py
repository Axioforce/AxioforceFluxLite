from __future__ import annotations

import csv
import os
from typing import Optional


class CsvTransformRepository:
    def downsample_csv_to_50hz(self, source_csv: str, dest_csv: str) -> str:
        os.makedirs(os.path.dirname(dest_csv), exist_ok=True)
        with open(source_csv, "r", newline="", encoding="utf-8") as fin, open(dest_csv, "w", newline="", encoding="utf-8") as fout:
            reader = csv.reader(fin)
            writer = csv.writer(fout)
            header = next(reader, None)
            if not header:
                raise ValueError("CSV header missing")
            writer.writerow(header)

            headers_map = {h.strip().lower(): i for i, h in enumerate(header)}
            time_idx = -1
            for k in ("time", "time_ms"):
                if k in headers_map:
                    time_idx = headers_map[k]
                    break

            if time_idx < 0:
                raise ValueError("CSV missing required 'time' column")

            last_t: Optional[float] = None
            target_interval = 20.0  # 50Hz = 20ms

            for row in reader:
                if len(row) <= time_idx:
                    continue
                try:
                    t_val = float(row[time_idx])
                except Exception:
                    continue

                if last_t is None:
                    writer.writerow(row)
                    last_t = t_val
                    continue

                if (t_val - last_t) >= target_interval:
                    writer.writerow(row)
                    last_t = t_val

        return dest_csv


