from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

from PySide6 import QtCore, QtWidgets

from ... import config


def _fmt_pct(v: float) -> str:
    if v is None or not isinstance(v, (int, float)) or math.isnan(float(v)):
        return "—"
    return f"{float(v):+.2f}%"


def _fmt_pct_abs(v: float) -> str:
    if v is None or not isinstance(v, (int, float)) or math.isnan(float(v)):
        return "—"
    return f"{abs(float(v)):.2f}%"


def _fmt_num(v: float) -> str:
    if v is None or not isinstance(v, (int, float)) or math.isnan(float(v)):
        return "—"
    return f"{float(v):.2f}"


def _fmt_pct_plain(v: float) -> str:
    if v is None or not isinstance(v, (int, float)) or math.isnan(float(v)):
        return "—"
    return f"{float(v):.2f}%"


def _pass_rate(count_pass: int, count_total: int) -> str:
    if count_total <= 0:
        return "—"
    return f"{100.0 * float(count_pass) / float(count_total):.1f}%"


def _compact_coef_label(label: str) -> str:
    """
    Convert stored coef labels/keys like:
      "legacy:x=3.000000,y=2.500000,z=0.800000"
    into:
      "3,2.5,0.8"
    """
    s = str(label or "").strip()
    if not s:
        return "—"
    try:
        # Drop "mode:" prefix if present.
        if ":" in s:
            s = s.split(":", 1)[1]
        parts = {}
        for p in s.split(","):
            p = p.strip()
            if "=" not in p:
                continue
            k, v = p.split("=", 1)
            k = k.strip().lower()
            parts[k] = float(v)
        xs = []
        for k in ("x", "y", "z"):
            v = float(parts.get(k, 0.0))
            out = f"{v:.6f}".rstrip("0").rstrip(".")
            if out == "-0":
                out = "0"
            xs.append(out)
        return ",".join(xs)
    except Exception:
        return str(label)


class TempTestingMetricsWidget(QtWidgets.QWidget):
    top3_sort_changed = QtCore.Signal(str)  # "mean_abs" | "signed_abs"
    post_correction_changed = QtCore.Signal(bool, float)
    """
    Metrics UI for the Temperature Testing tab.

    Current view section shows:
      - Bias-controlled baseline health (bias mean/std, measured cell counts)
      - Selected OFF vs ON stats (mean abs %, mean signed %, pass rate) per stage + All
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # --- Current view ---
        current_box = QtWidgets.QGroupBox("Current View")
        current_layout = QtWidgets.QVBoxLayout(current_box)
        current_layout.setSpacing(8)

        # Bias health table
        bias_box = QtWidgets.QGroupBox("Bias Baseline Health (room-temp)")
        bias_layout = QtWidgets.QGridLayout(bias_box)
        bias_layout.setHorizontalSpacing(10)
        bias_layout.setVerticalSpacing(4)

        bias_layout.addWidget(QtWidgets.QLabel("Stage"), 0, 0)
        bias_layout.addWidget(QtWidgets.QLabel("Mean signed"), 0, 1)
        bias_layout.addWidget(QtWidgets.QLabel("Std (signed)"), 0, 2)
        bias_layout.addWidget(QtWidgets.QLabel("Cells measured"), 0, 3)

        self._bias_labels: Dict[str, Tuple[QtWidgets.QLabel, QtWidgets.QLabel, QtWidgets.QLabel]] = {}
        for row, key in enumerate(("all", "db", "bw"), start=1):
            name = "All" if key == "all" else ("45lb" if key == "db" else "Bodyweight")
            bias_layout.addWidget(QtWidgets.QLabel(name), row, 0)
            mean_lbl = QtWidgets.QLabel("—")
            std_lbl = QtWidgets.QLabel("—")
            n_lbl = QtWidgets.QLabel("—")
            bias_layout.addWidget(mean_lbl, row, 1)
            bias_layout.addWidget(std_lbl, row, 2)
            bias_layout.addWidget(n_lbl, row, 3)
            self._bias_labels[key] = (mean_lbl, std_lbl, n_lbl)

        # Selected OFF/ON table
        run_box = QtWidgets.QGroupBox("Selected Run (OFF vs ON)")
        run_layout = QtWidgets.QGridLayout(run_box)
        run_layout.setHorizontalSpacing(10)
        run_layout.setVerticalSpacing(4)

        headers = ["Stage", "OFF mean abs", "OFF mean signed", "OFF pass", "ON mean abs", "ON mean signed", "ON pass"]
        for col, h in enumerate(headers):
            run_layout.addWidget(QtWidgets.QLabel(h), 0, col)

        self._run_labels: Dict[str, Dict[str, QtWidgets.QLabel]] = {}
        for row, key in enumerate(("all", "db", "bw"), start=1):
            name = "All" if key == "all" else ("45lb" if key == "db" else "Bodyweight")
            run_layout.addWidget(QtWidgets.QLabel(name), row, 0)
            row_labels: Dict[str, QtWidgets.QLabel] = {}
            for col_key, col in [
                ("off_abs", 1),
                ("off_signed", 2),
                ("off_pass", 3),
                ("on_abs", 4),
                ("on_signed", 5),
                ("on_pass", 6),
            ]:
                lbl = QtWidgets.QLabel("—")
                run_layout.addWidget(lbl, row, col)
                row_labels[col_key] = lbl
            self._run_labels[key] = row_labels

        current_layout.addWidget(bias_box)
        current_layout.addWidget(run_box)

        # We'll place Current View and Big Picture side-by-side to save vertical space.

        # --- Big picture ---
        big_box = QtWidgets.QGroupBox("Big Picture (Plate Type)")
        big_layout = QtWidgets.QVBoxLayout(big_box)
        big_layout.setSpacing(6)

        self.lbl_big_status = QtWidgets.QLabel("—")
        big_layout.addWidget(self.lbl_big_status)

        # Post-processing correction controls
        corr_row = QtWidgets.QHBoxLayout()
        corr_row.setSpacing(6)
        self.chk_post_correction = QtWidgets.QCheckBox("Post-correction")
        self.chk_post_correction.setToolTip(
            "Apply post-processing correction:\n"
            "Fz,c = Fz(1 + deltaT * k * ((|Fz| - Fref)/Fref))"
        )
        self.spin_post_correction_k = QtWidgets.QDoubleSpinBox()
        self.spin_post_correction_k.setRange(0.0, 2.0)
        self.spin_post_correction_k.setSingleStep(0.000001)
        self.spin_post_correction_k.setDecimals(6)
        self.spin_post_correction_k.setValue(0.0)
        self.spin_post_correction_k.setToolTip("k gain (0–2)")
        corr_row.addWidget(self.chk_post_correction, 0)
        corr_row.addWidget(QtWidgets.QLabel("k:"), 0)
        corr_row.addWidget(self.spin_post_correction_k, 0)
        corr_row.addStretch(1)
        big_layout.addLayout(corr_row)

        # Unified + k summary (from stage-split MAE report)
        uk_box = QtWidgets.QGroupBox("Unified + k (Stage-split MAE)")
        uk_layout = QtWidgets.QGridLayout(uk_box)
        uk_layout.setHorizontalSpacing(10)
        uk_layout.setVerticalSpacing(4)
        for col, h in enumerate(["Coef", "k", "Mean abs %", "Mean signed %", "Std %", "N"]):
            uk_layout.addWidget(QtWidgets.QLabel(h), 0, col)
        self._unified_k_labels: Dict[str, QtWidgets.QLabel] = {}
        for idx, key in enumerate(["coef", "k", "mean_abs", "mean_signed", "std_signed", "n"]):
            lbl = QtWidgets.QLabel("—")
            uk_layout.addWidget(lbl, 1, idx)
            self._unified_k_labels[key] = lbl
        big_layout.addWidget(uk_box)

        # Auto search controls
        auto_row = QtWidgets.QHBoxLayout()
        auto_row.setSpacing(6)
        auto_row.addWidget(QtWidgets.QLabel("Auto Search:"), 0)
        self.auto_search_combo = QtWidgets.QComboBox()
        self.auto_search_combo.addItems(["Unified", "Distinct Coefs", "Unified + k (Stage-split MAE)"])
        self.auto_search_combo.setToolTip(
            "Automated coefficient search.\n"
            "- Unified: X=Y=Z and find closest-to-zero signed bias for the plate type\n"
            "- Distinct Coefs: explore X/Y/Z independently (18-candidate neighborhood report)\n"
            "- Unified + k (stage-split MAE): for each non-baseline test, find best unified coef for BW stage and 45lb stage separately and export a CSV"
        )
        auto_row.addWidget(self.auto_search_combo, 1)
        self.btn_auto_search = QtWidgets.QPushButton("Run")
        self.btn_auto_search.setToolTip("Run auto search for the current plate type and store results in the rollup.")
        auto_row.addWidget(self.btn_auto_search, 0)
        big_layout.addLayout(auto_row)

        # Simple top-3 display (placeholders for now; controller will fill these)
        top_box = QtWidgets.QGroupBox("Top 3 Coef Combos (Bias Controlled)")
        top_layout = QtWidgets.QGridLayout(top_box)
        top_layout.setHorizontalSpacing(10)
        top_layout.setVerticalSpacing(4)
        top_layout.addWidget(QtWidgets.QLabel("#"), 0, 0)
        top_layout.addWidget(QtWidgets.QLabel("Coef"), 0, 1)
        self._top3_sort_mode = "mean_abs"
        self._top3_mean_abs_rows: list[dict] = []
        self._top3_signed_abs_rows: list[dict] = []

        def _mk_header_btn(text: str) -> QtWidgets.QPushButton:
            b = QtWidgets.QPushButton(text)
            b.setFlat(True)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setStyleSheet("QPushButton{padding:0px;text-align:left;}")
            return b

        self._hdr_mean_abs = _mk_header_btn("Score (mean abs %)")
        self._hdr_signed = _mk_header_btn("Signed %")
        self._hdr_mean_abs.clicked.connect(lambda: self._set_top3_sort_mode("mean_abs"))
        self._hdr_signed.clicked.connect(lambda: self._set_top3_sort_mode("signed_abs"))
        top_layout.addWidget(self._hdr_mean_abs, 0, 2)
        top_layout.addWidget(self._hdr_signed, 0, 3)
        top_layout.addWidget(QtWidgets.QLabel("Std %"), 0, 4)
        top_layout.addWidget(QtWidgets.QLabel("Coverage"), 0, 5)

        self._top_rows: list[tuple[QtWidgets.QLabel, QtWidgets.QLabel, QtWidgets.QLabel, QtWidgets.QLabel, QtWidgets.QLabel]] = []
        for i in range(1, 4):
            top_layout.addWidget(QtWidgets.QLabel(str(i)), i, 0)
            coef = QtWidgets.QLabel("—")
            score = QtWidgets.QLabel("—")
            signed = QtWidgets.QLabel("—")
            std = QtWidgets.QLabel("—")
            cov = QtWidgets.QLabel("—")
            top_layout.addWidget(coef, i, 1)
            top_layout.addWidget(score, i, 2)
            top_layout.addWidget(signed, i, 3)
            top_layout.addWidget(std, i, 4)
            top_layout.addWidget(cov, i, 5)
            self._top_rows.append((coef, score, signed, std, cov))

        big_layout.addWidget(top_box)

        self.btn_reset_top3 = QtWidgets.QPushButton("Reset top 3 (clear plate-type rollup)")
        self.btn_reset_top3.setToolTip(
            "Clears the stored plate-type rollup used to compute the Top 3 list. This does not delete raw tests, only the rollup cache."
        )
        big_layout.addWidget(self.btn_reset_top3)

        self._update_top3_header_styles()
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(current_box)
        splitter.addWidget(big_box)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        root.addWidget(splitter, 1)

        self.chk_post_correction.toggled.connect(lambda _v: self._emit_post_correction_changed())
        self.spin_post_correction_k.valueChanged.connect(lambda _v: self._emit_post_correction_changed())

    def clear(self) -> None:
        for lbls in self._bias_labels.values():
            for lbl in lbls:
                lbl.setText("—")
        for row in self._run_labels.values():
            for lbl in row.values():
                lbl.setText("—")
        try:
            self.lbl_big_status.setText("—")
        except Exception:
            pass
        for coef, score, signed, std, cov in getattr(self, "_top_rows", []):
            coef.setText("—")
            score.setText("—")
            signed.setText("—")
            std.setText("—")
            cov.setText("—")
        for lbl in getattr(self, "_unified_k_labels", {}).values():
            lbl.setText("—")

    def set_big_picture_status(self, text: str) -> None:
        try:
            self.lbl_big_status.setText(str(text or "—"))
        except Exception:
            pass

    def set_unified_k_summary(self, summary: Optional[dict]) -> None:
        labels = getattr(self, "_unified_k_labels", {}) or {}
        if not isinstance(summary, dict):
            for lbl in labels.values():
                lbl.setText("—")
            return
        try:
            coef_label = summary.get("coef_key")
            if coef_label:
                # Unified coefs: show c with 0.0001 granularity
                try:
                    coef_val = float(summary.get("coef") or 0.0)
                    labels.get("coef", QtWidgets.QLabel()).setText(f"{coef_val:.4f}")
                except Exception:
                    labels.get("coef", QtWidgets.QLabel()).setText(_compact_coef_label(str(coef_label)))
            else:
                labels.get("coef", QtWidgets.QLabel()).setText(_fmt_num(summary.get("coef")))
            try:
                labels.get("k", QtWidgets.QLabel()).setText(f"{float(summary.get('k') or 0.0):.6f}")
            except Exception:
                labels.get("k", QtWidgets.QLabel()).setText(_fmt_num(summary.get("k")))
            labels.get("mean_abs", QtWidgets.QLabel()).setText(_fmt_pct_abs(summary.get("mean_abs")))
            labels.get("mean_signed", QtWidgets.QLabel()).setText(_fmt_pct(summary.get("mean_signed")))
            labels.get("std_signed", QtWidgets.QLabel()).setText(_fmt_pct_plain(summary.get("std_signed")))
            labels.get("n", QtWidgets.QLabel()).setText(str(summary.get("n") or "—"))
        except Exception:
            for lbl in labels.values():
                lbl.setText("—")

    def set_post_correction_k(self, k: Optional[float]) -> None:
        if k is None:
            return
        try:
            self.spin_post_correction_k.setValue(float(k))
        except Exception:
            pass

    def post_correction_settings(self) -> tuple[bool, float]:
        try:
            return bool(self.chk_post_correction.isChecked()), float(self.spin_post_correction_k.value())
        except Exception:
            return False, 0.0

    def _emit_post_correction_changed(self) -> None:
        try:
            enabled, k = self.post_correction_settings()
            self.post_correction_changed.emit(enabled, k)
        except Exception:
            pass

    def set_top3(self, rows_mean_abs: list[dict], rows_signed_abs: list[dict]) -> None:
        """
        rows_*: list of { coef_label, score_mean_abs, mean_signed, std_signed, coverage }
        """
        self._top3_mean_abs_rows = list(rows_mean_abs or [])
        self._top3_signed_abs_rows = list(rows_signed_abs or [])
        self._render_top3()

    def _set_top3_sort_mode(self, mode: str) -> None:
        mode = str(mode or "mean_abs").strip().lower()
        if mode not in ("mean_abs", "signed_abs"):
            mode = "mean_abs"
        if getattr(self, "_top3_sort_mode", "mean_abs") == mode:
            return
        self._top3_sort_mode = mode
        self._update_top3_header_styles()
        try:
            self.top3_sort_changed.emit(mode)
        except Exception:
            pass
        self._render_top3()

    def top3_sort_mode(self) -> str:
        return str(getattr(self, "_top3_sort_mode", "mean_abs") or "mean_abs")

    def _update_top3_header_styles(self) -> None:
        mode = self.top3_sort_mode()
        def _style(active: bool) -> str:
            return "QPushButton{font-weight:%s; padding:0px; text-align:left;}" % ("700" if active else "400")
        try:
            self._hdr_mean_abs.setStyleSheet(_style(mode == "mean_abs"))
            self._hdr_signed.setStyleSheet(_style(mode == "signed_abs"))
        except Exception:
            pass

    def _render_top3(self) -> None:
        rows = self._top3_mean_abs_rows if self.top3_sort_mode() == "mean_abs" else self._top3_signed_abs_rows
        for idx, widgets in enumerate(getattr(self, "_top_rows", [])):
            coef, score, signed, std, cov = widgets
            if idx >= len(rows):
                coef.setText("—")
                score.setText("—")
                signed.setText("—")
                std.setText("—")
                cov.setText("—")
                continue
            r = rows[idx] or {}
            coef.setText(_compact_coef_label(str(r.get("coef_label") or r.get("coef_key") or "")))
            score.setText(f"{float(r.get('score_mean_abs') or 0.0):.2f}%")
            signed.setText(f"{float(r.get('mean_signed') or 0.0):+.2f}%")
            std.setText(f"{float(r.get('std_signed') or 0.0):.2f}%")
            cov.setText(str(r.get("coverage") or "—"))

    def set_bias_cache(self, bias_cache: Optional[dict]) -> None:
        """
        Update bias health metrics from the cached baseline bias payload.
        """
        if not isinstance(bias_cache, dict):
            for key in self._bias_labels:
                self._bias_labels[key][0].setText("—")
                self._bias_labels[key][1].setText("—")
                self._bias_labels[key][2].setText("—")
            return

        bias_all = bias_cache.get("bias_all") or bias_cache.get("bias")
        bias_db = bias_cache.get("bias_db")
        bias_bw = bias_cache.get("bias_bw")
        measured = bias_cache.get("measured_cells") or {}

        def _stats(bias_map: Any) -> Tuple[float, float]:
            vals = []
            if isinstance(bias_map, list):
                for row in bias_map:
                    if not isinstance(row, list):
                        continue
                    for v in row:
                        try:
                            vals.append(float(v) * 100.0)
                        except Exception:
                            continue
            if not vals:
                return 0.0, 0.0
            mean = sum(vals) / float(len(vals))
            var = sum((x - mean) ** 2 for x in vals) / float(max(1, len(vals) - 1))
            return mean, math.sqrt(max(0.0, var))

        mean_all, std_all = _stats(bias_all)
        mean_db, std_db = _stats(bias_db)
        mean_bw, std_bw = _stats(bias_bw)

        def _measured(stage_key: str) -> str:
            info = measured.get(stage_key) if isinstance(measured, dict) else None
            if not isinstance(info, dict):
                return "—"
            # Show mean measured cells across baselines; include min/max if available.
            try:
                m = float(info.get("mean") or 0.0)
                mn = float(info.get("min") or 0.0)
                mx = float(info.get("max") or 0.0)
                if mx and mn and (mx != mn):
                    return f"{m:.1f} ({mn:.0f}–{mx:.0f})"
                return f"{m:.1f}"
            except Exception:
                return "—"

        self._bias_labels["all"][0].setText(_fmt_pct(mean_all))
        self._bias_labels["all"][1].setText(_fmt_pct_plain(std_all))
        # "All" measured cells: average of db/bw means (best-effort)
        try:
            db_info = measured.get("db") if isinstance(measured, dict) else None
            bw_info = measured.get("bw") if isinstance(measured, dict) else None
            db_mean = float((db_info or {}).get("mean") or 0.0) if isinstance(db_info, dict) else 0.0
            bw_mean = float((bw_info or {}).get("mean") or 0.0) if isinstance(bw_info, dict) else 0.0
            if db_mean and bw_mean:
                self._bias_labels["all"][2].setText(f"{0.5 * (db_mean + bw_mean):.1f}")
            else:
                self._bias_labels["all"][2].setText("—")
        except Exception:
            self._bias_labels["all"][2].setText("—")

        self._bias_labels["db"][0].setText(_fmt_pct(mean_db))
        self._bias_labels["db"][1].setText(_fmt_pct_plain(std_db))
        self._bias_labels["db"][2].setText(_measured("db"))

        self._bias_labels["bw"][0].setText(_fmt_pct(mean_bw))
        self._bias_labels["bw"][1].setText(_fmt_pct_plain(std_bw))
        self._bias_labels["bw"][2].setText(_measured("bw"))

    def set_run_metrics(
        self,
        analysis_payload: Optional[dict],
        *,
        device_type: str,
        body_weight_n: float,
        bias_map_all: Any = None,
        grading_mode: str = "absolute",
    ) -> None:
        """
        Update OFF vs ON metrics from a temperature analysis payload (baseline vs selected).
        OFF = payload["baseline"], ON = payload["selected"].
        """
        if not isinstance(analysis_payload, dict):
            for key in self._run_labels:
                for lbl in self._run_labels[key].values():
                    lbl.setText("—")
            return

        bias_enabled = str(grading_mode or "").strip().lower().startswith("bias") and bias_map_all is not None

        baseline = analysis_payload.get("baseline") or {}
        selected = analysis_payload.get("selected") or {}

        def _compute(run_data: dict, stage_key: str) -> dict:
            stages = (run_data or {}).get("stages") or {}
            keys = list(stages.keys()) if stage_key == "all" else [stage_key]
            abs_pcts = []
            signed_pcts = []
            pass_count = 0
            total = 0

            for sk in keys:
                stage = stages.get(sk) or {}
                base_target = float(stage.get("target_n") or 0.0)
                threshold = float(config.get_passing_threshold(sk, device_type, float(body_weight_n or 0.0)))
                for cell in stage.get("cells", []) or []:
                    try:
                        r = int(cell.get("row", 0))
                        c = int(cell.get("col", 0))
                        mean_n = float(cell.get("mean_n", 0.0))
                    except Exception:
                        continue
                    target = base_target
                    if bias_enabled:
                        try:
                            target = base_target * (1.0 + float(bias_map_all[r][c]))
                        except Exception:
                            target = base_target
                    if not target:
                        continue
                    signed = (mean_n - target) / target * 100.0
                    abs_pcts.append(abs(signed))
                    signed_pcts.append(signed)
                    total += 1

                    # Pass: within 1.0T (light_green or better)
                    err_ratio = abs(mean_n - target) / threshold if threshold > 0 else 999.0
                    if err_ratio <= float(config.COLOR_BIN_MULTIPLIERS.get("light_green", 1.0)):
                        pass_count += 1

            if not abs_pcts:
                return {"mean_abs": None, "mean_signed": None, "pass_rate": None}

            mean_abs = sum(abs_pcts) / float(len(abs_pcts))
            mean_signed = sum(signed_pcts) / float(len(signed_pcts))
            return {"mean_abs": mean_abs, "mean_signed": mean_signed, "pass_rate": _pass_rate(pass_count, total)}

        for key in ("all", "db", "bw"):
            off = _compute(baseline, key)
            on = _compute(selected, key)
            row = self._run_labels[key]
            row["off_abs"].setText(_fmt_pct_abs(off.get("mean_abs")))
            row["off_signed"].setText(_fmt_pct(off.get("mean_signed")))
            row["off_pass"].setText(str(off.get("pass_rate") or "—"))
            row["on_abs"].setText(_fmt_pct_abs(on.get("mean_abs")))
            row["on_signed"].setText(_fmt_pct(on.get("mean_signed")))
            row["on_pass"].setText(str(on.get("pass_rate") or "—"))


