## Gain analysis (discrete temp datasets)

This tooling measures the effective system gain:

`gain = (output_change_pct) / (intended_input_change_pct)`

Where:
- **output** = processed `sum-z` (NN / backend output)
- **intended input** = percent change in `L1_z = sum(|z_i|)` across the 8 raw sensors after applying the same per-sensor temperature scaling used by the backend.

### Why this exists
Discrete temp tests produce **raw** sensor data (no NN). The backend uses a neural network and can respond non-linearly to input scaling. This tool quantifies that response, so we can later derive a more accurate coefficient mapping.

### Usage

Run from repo root:

```bash
python -m analysis.gain_analysis.gain_analysis --data-root discrete_temp_testing --room-temp-f 76.0 --coef-sweep 0.001:0.010:0.001
```

Optional:
- `--host http://localhost`
- `--port 3000`
- `--out-dir analysis/gain_analysis_output`
- `--limit-files 5`

### Outputs
- `analysis/gain_analysis_output/gain_rows.csv`: row-level gain records (one per raw row per coefficient).
- `analysis/gain_analysis_output/gain_summary.csv`: aggregated gain stats by plate/device/phase/coef/temp bucket.


