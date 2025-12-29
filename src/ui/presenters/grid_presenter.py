from __future__ import annotations
from typing import Dict, List, Tuple, Any
from dataclasses import dataclass
from PySide6 import QtGui

from ... import config

from ...domain.testing import TestResult, TestThresholds

@dataclass
class CellViewModel:
    row: int
    col: int
    color: QtGui.QColor
    text: str
    tooltip: str = ""

class GridPresenter:
    """
    Transforms data (analysis results, test results) into UI view models (colors, text).
    Decouples business logic from Qt presentation details.
    """

    def compute_live_cell(self, result: TestResult, target_n: float, tolerance_n: float) -> CellViewModel:
        """
        Compute view model for a live test result.
        """
        mean_n = result.fz_mean_n if result.fz_mean_n is not None else 0.0
        
        # Calculate error ratio
        error_n = abs(mean_n - target_n)
        error_ratio = error_n / tolerance_n if tolerance_n > 0 else 0.0
        
        color_bin = config.get_color_bin(error_ratio)
        color = self.get_color_for_bin(color_bin)
        
        # Text? maybe just the force?
        text = f"{mean_n:.1f}N"
        
        return CellViewModel(
            row=result.row,
            col=result.col,
            color=color,
            text=text,
            tooltip=f"Target: {target_n:.1f}N, Actual: {mean_n:.1f}N"
        )

    def compute_temperature_grid(
        self, 
        analysis_data: Dict[str, Any], 
        stage_key: str, 
        body_weight_n: float
    ) -> List[CellViewModel]:
        """
        Convert temperature analysis payload into grid cells.
        """
        if not analysis_data:
            return []

        stages = analysis_data.get("stages", {})
        grid_info = analysis_data.get("grid", {}) # analysis payload has grid info? Yes, check analyzer output.
        # Analyzer output: {"grid": {rows, cols, device_type}, ... "baseline": {stages...}, ...}
        # Wait, compute_temperature_grid in Controller took `data` which was `baseline` or `selected`.
        # So I should pass that specific dict.
        
        # We need device_type to get thresholds.
        # It's not in the `data` dict (stage map), it was passed separately in Controller.
        # I'll rely on caller passing device_type or extracting it if present.
        # In Controller: `device_type = str(grid_info.get("device_type", "06"))`
        
        # Let's update signature to match usage or better.
        pass

    def compute_analysis_cells(
        self,
        data: Dict[str, Any], # 'baseline' or 'selected' dict from analysis
        stage_key: str,
        device_type: str,
        body_weight_n: float,
        *,
        bias_map: Any = None,
    ) -> List[CellViewModel]:
        """
        Compute display data for cells from analysis data (baseline or selected).
        """
        stages = data.get("stages", {})
        cell_data: Dict[Tuple[int, int], Dict] = {}
        
        stage_keys = list(stages.keys()) if stage_key == "All" else [stage_key]
        
        for sk in stage_keys:
            stage_info = stages.get(sk, {})
            if not stage_info:
                continue
                
            base_target_n = float(stage_info.get("target_n", 0.0))
            threshold_n = config.get_passing_threshold(sk, device_type, body_weight_n)
            
            for cell in stage_info.get("cells", []):
                r = int(cell.get("row", 0))
                c = int(cell.get("col", 0))
                mean_n = float(cell.get("mean_n", 0.0))

                # Bias-controlled grading adjusts the target per cell; thresholds remain unchanged.
                target_n = base_target_n
                if bias_map is not None:
                    try:
                        bias_pct = float(bias_map[r][c])
                        target_n = base_target_n * (1.0 + bias_pct)
                    except Exception:
                        target_n = base_target_n

                signed_pct = 0.0
                if target_n:
                    signed_pct = (mean_n - target_n) / target_n * 100.0
                
                # Compute error ratio
                error_n = abs(mean_n - target_n)
                error_ratio = error_n / threshold_n if threshold_n > 0 else 0.0
                
                key = (r, c)
                if key not in cell_data:
                    cell_data[key] = {"signed_pcts": [], "error_ratios": []}
                cell_data[key]["signed_pcts"].append(signed_pct)
                cell_data[key]["error_ratios"].append(error_ratio)
        
        # Build result list
        result: List[CellViewModel] = []
        for (r, c), info in cell_data.items():
            avg_pct = sum(info["signed_pcts"]) / len(info["signed_pcts"])
            avg_ratio = sum(info["error_ratios"]) / len(info["error_ratios"])
            
            color_bin = config.get_color_bin(avg_ratio)
            rgba = config.COLOR_BIN_RGBA.get(color_bin, (0, 200, 0, 180))
            color = QtGui.QColor(*rgba)
            text = f"{avg_pct:+.1f}%"
            
            result.append(CellViewModel(
                row=r,
                col=c,
                color=color,
                text=text,
                tooltip=f"Error Ratio: {avg_ratio:.2f}"
            ))
        
        return result

    def get_color_for_bin(self, bin_name: str) -> QtGui.QColor:
        rgba = config.COLOR_BIN_RGBA.get(bin_name, (0, 200, 0, 180))
        return QtGui.QColor(*rgba)

