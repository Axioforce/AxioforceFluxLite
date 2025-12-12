import csv
import os
import matplotlib.pyplot as plt
import matplotlib.patches as patches

def plot_stage_comparison(
    baseline_path: str,
    selected_path: str,
    body_weight_n: float,
    baseline_windows: dict = None,
    baseline_segments: list = None,
    selected_windows: dict = None
):
    """
    Plots baseline vs selected CSVs with window visualization.
    
    Args:
        baseline_path: Path to baseline CSV
        selected_path: Path to selected CSV
        body_weight_n: Body weight in Newtons (for reference lines)
        baseline_windows: Dict of best windows {stage_key: {cell: {t_start, t_end, ...}}}
        baseline_segments: List of candidate segments [{t_start, t_end, ...}]
        selected_windows: Dict of best windows for selected run
    """
    
    # Load data
    t_base, fz_base = _load_csv_data(baseline_path)
    t_sel, fz_sel = _load_csv_data(selected_path)
    
    if not t_base and not t_sel:
        print("No data to plot")
        return

    # Determine global t0 from baseline if possible, else selected
    t0 = 0.0
    if t_base and hasattr(t_base[0], 'real') and not (t_base[0] != t_base[0]):
        t0 = t_base[0]
    elif t_sel and hasattr(t_sel[0], 'real') and not (t_sel[0] != t_sel[0]):
        t0 = t_sel[0]

    # Normalize time to start at 0 for readability
    if t_base:
        t_base = [(t - t0) if (t == t) else t for t in t_base]
            
    if t_sel:
        # Normalize selected by the SAME t0 so they align if they are from same session
        t_sel = [(t - t0) if (t == t) else t for t in t_sel]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True, sharey=True) # Share X and Y to keep synchronized
    fig.canvas.manager.set_window_title("Stage Detection Analysis")
    
    # Track counts for title
    base_counts = {"db": 0, "bw": 0}
    sel_counts = {"db": 0, "bw": 0}

    # Plot Baseline
    if t_base:
        ax1.plot(t_base, fz_base, 'b-', label='Baseline Fz', alpha=0.7, linewidth=1)
        ax1.set_title(f"Baseline: {os.path.basename(baseline_path)}")
        ax1.set_ylabel("Force (N)")
        ax1.grid(True, alpha=0.3)
        
        if baseline_segments:
            for seg in baseline_segments:
                t_start = seg.get("t_start", 0) - t0
                t_end = seg.get("t_end", 0) - t0
                ax1.axvspan(t_start, t_end, color='gray', alpha=0.2, ymin=0, ymax=1)
                
        if baseline_windows:
            _plot_windows(ax1, baseline_windows, t0, base_counts)
        
        # Add counts to title
        c_db = base_counts.get("db", 0)
        c_bw = base_counts.get("bw", 0)
        ax1.set_title(f"Baseline: {os.path.basename(baseline_path)} (Windows: DB={c_db}, BW={c_bw})")

    # Plot Selected
    if t_sel:
        ax2.plot(t_sel, fz_sel, 'r-', label='Selected Fz', alpha=0.7, linewidth=1)
        ax2.set_title(f"Selected: {os.path.basename(selected_path)}")
        ax2.set_ylabel("Force (N)")
        ax2.set_xlabel("Time (ms)")
        ax2.grid(True, alpha=0.3)
        
        if selected_windows:
             _plot_windows(ax2, selected_windows, t0, sel_counts)

        # Add counts to title
        c_db = sel_counts.get("db", 0)
        c_bw = sel_counts.get("bw", 0)
        ax2.set_title(f"Selected: {os.path.basename(selected_path)} (Windows: DB={c_db}, BW={c_bw})")

    # Add reference lines if meaningful
    for ax in (ax1, ax2):
        ax.axhline(0, color='k', linewidth=0.5)
        if body_weight_n > 0:
            ax.axhline(body_weight_n, color='m', linestyle='--', alpha=0.5, label='Body Weight')

    plt.tight_layout()
    plt.show()

def _plot_windows(ax, windows_dict, t_offset, counts_out):
    """Helper to iterate nested window dict and plot spans."""
    # Define colors for stages
    colors = {
        "db": "orange",
        "bw": "purple",
    }
    
    # Track which labels we've added to avoid legend dupes
    added_labels = set()
    
    for stage_key, cells in windows_dict.items():
        # Clean key for matching (handle '45 lb DB' vs 'db')
        key_norm = "db" if "db" in stage_key.lower() or "45" in stage_key else "bw"
        if "body" in stage_key.lower(): key_norm = "bw"
        
        color = colors.get(key_norm, "green")
        
        for cell_key, win in cells.items():
            t_start = win.get("t_start")
            t_end = win.get("t_end")
            
            if t_start is not None and t_end is not None:
                # Update counter
                counts_out[key_norm] = counts_out.get(key_norm, 0) + 1
                
                # Apply offset
                t_start -= t_offset
                t_end -= t_offset
                
                lbl = None
                if key_norm not in added_labels:
                    lbl = f"{key_norm.upper()} Window"
                    added_labels.add(key_norm)
                
                ax.axvspan(t_start, t_end, color=color, alpha=0.4, label=lbl)
    
    # Force legend to show the new labels
    ax.legend(loc="upper right")

def _load_csv_data(path):
    times = []
    fz_vals = []
    if not path or not os.path.exists(path):
        return times, fz_vals
        
    try:
        with open(path, "r", newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            header = next(reader, [])
            if not header:
                return times, fz_vals
            
            headers_map = {h.strip().lower(): i for i, h in enumerate(header)}
            
            time_idx = -1
            for k in ("time", "time_ms", "elapsed_time"):
                if k in headers_map:
                    time_idx = headers_map[k]
                    break
            
            fz_idx = -1
            for k in ("sum-z", "sum_z", "fz"):
                if k in headers_map:
                    fz_idx = headers_map[k]
                    break
            
            if time_idx < 0 or fz_idx < 0:
                return times, fz_vals
            
            last_t = None
            gap_threshold = 200.0  # ms
            
            for row in reader:
                if len(row) <= max(time_idx, fz_idx):
                    continue
                try:
                    t = float(row[time_idx])
                    fz = float(row[fz_idx])
                    
                    # Insert NaN to break line if gap is too large (e.g. missing data)
                    if last_t is not None and (t - last_t) > gap_threshold:
                        times.append(float('nan'))
                        fz_vals.append(float('nan'))
                    
                    times.append(t)
                    fz_vals.append(fz)
                    last_t = t
                except Exception:
                    continue
    except Exception as e:
        print(f"Error reading CSV {path}: {e}")
        
    return times, fz_vals
