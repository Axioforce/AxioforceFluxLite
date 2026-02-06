from __future__ import annotations

import json
from typing import Any, Iterable

import streamlit as st

from tools.MetricsEditor import paths
from tools.MetricsEditor import metric_push_pipeline
from tools.MetricsEditor.normalization import CHOICES, normalize_optimization_mode
from tools.MetricsEditor.llm_prompt import is_incomplete


def _ensure_list(x: Any) -> list[str]:
    if x is None:
        return []
    if isinstance(x, list):
        return [str(i) for i in x if str(i).strip()]
    return [str(x)] if str(x).strip() else []


def _csv_list(s: str) -> list[str]:
    # Accept commas + newlines
    items: list[str] = []
    for part in (s or "").replace("\n", ",").split(","):
        t = part.strip()
        if t:
            items.append(t)
    # de-dupe preserving order
    out: list[str] = []
    seen: set[str] = set()
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


def _pretty_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def render_metric_form(
    *,
    axf_id: str,
    metric: dict[str, Any],
    capture_types: Iterable[str],
    metric_choices: list[str] | None = None,
    metric_label_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Render a structured editor for a metric. Returns a NEW metric dict with edits applied.
    """
    capture_types = list(capture_types)
    m = dict(metric or {})
    m["axf_id"] = axf_id

    # Known fields (safe defaults)
    name = str(m.get("name") or "")
    description = str(m.get("description") or "")
    units = m.get("units")
    units_str = "" if units is None else str(units)
    equation = str(m.get("equation") or "")
    script = str(m.get("script") or "")

    required_components = _ensure_list(m.get("required_components"))
    required_metrics = _ensure_list(m.get("required_metrics"))
    required_devices = _ensure_list(m.get("required_devices"))

    latex_formula = m.get("latex_formula")
    latex_formula_str = "" if latex_formula is None else str(latex_formula)

    # Per-capture-type overrides (dicts)
    optimization_mode = m.get("optimization_mode") if isinstance(m.get("optimization_mode"), dict) else {}
    equation_explanation = m.get("equation_explanation") if isinstance(m.get("equation_explanation"), dict) else {}
    capture_type_info = m.get("capture_type_info") if isinstance(m.get("capture_type_info"), dict) else {}

    def _render_json_value(v: Any) -> None:
        if isinstance(v, (dict, list)):
            st.json(v)
            return
        try:
            st.json(json.dumps(v, ensure_ascii=False))
        except Exception:
            st.code(repr(v))

    left, right = st.columns([0.36, 0.64], gap="large")

    with left:
        st.markdown("**Fields**")
        field = st.radio(
            "Field",
            options=[
                "Basics",
                "Dependencies",
                "LaTeX",
                "Capture-type overrides",
                "Raw JSON (advanced)",
            ],
            label_visibility="collapsed",
        )

        st.markdown("**Pull from DB**")
        st.caption("Overwrite this metric's local data with the version from Firebase.")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Prod", key=f"pull_prod_{axf_id}"):
                import subprocess
                import os
                import sys

                env = os.environ.copy()
                env.setdefault("APP_ENV", "development")
                env["PYTHONPATH"] = str(paths.dynamo_root())
                env.pop("AXF_FIREBASE_CRED", None)  # Prod
                cwd = str(paths.dynamo_root() / "app")
                code = f"""
import json
from pathlib import Path
from app.db import db_hub
from app.db.firebase_utils import convert_firebase_admin_response

axf_id = {json.dumps(axf_id)}
fb = db_hub.firebase_hub
path = f'{{fb.paths["analytics"]}}/analytic'
doc = fb.database.collection(path).document(axf_id).get()
if not doc.exists:
    raise RuntimeError(f"Analytic not found: {{axf_id}}")
data = convert_firebase_admin_response(doc.to_dict() or {{}})
data["axf_id"] = axf_id
base_dir = Path("../file_system/analytics_db")
truth_dir = Path("../file_system/metrics_truth")
base_dir.mkdir(parents=True, exist_ok=True)
truth_dir.mkdir(parents=True, exist_ok=True)
(base_dir / f"{{axf_id}}.json").write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
(truth_dir / f"{{axf_id}}.json").write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
print(f"Pulled {{axf_id}} from prod")
"""
                try:
                    result = subprocess.run(
                        [sys.executable, "-c", code],
                        cwd=cwd,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=90,
                    )
                    if result.returncode == 0:
                        st.success("Pulled from prod.")
                        st.rerun()
                    else:
                        st.error(f"Pull failed: {result.stderr}")
                except subprocess.TimeoutExpired:
                    st.error("Pull timed out.")
        with col2:
            if st.button("Dev", key=f"pull_dev_{axf_id}"):
                import subprocess
                import os
                import sys

                env = os.environ.copy()
                env.setdefault("APP_ENV", "development")
                env["PYTHONPATH"] = str(paths.dynamo_root())
                env["AXF_FIREBASE_CRED"] = str(paths.dynamo_root() / "file_system" / "firebase-dev-key.json")
                cwd = str(paths.dynamo_root() / "app")
                code = f"""
import json
from pathlib import Path
from app.db import db_hub
from app.db.firebase_utils import convert_firebase_admin_response

axf_id = {json.dumps(axf_id)}
fb = db_hub.firebase_hub
path = f'{{fb.paths["analytics"]}}/analytic'
doc = fb.database.collection(path).document(axf_id).get()
if not doc.exists:
    raise RuntimeError(f"Analytic not found: {{axf_id}}")
data = convert_firebase_admin_response(doc.to_dict() or {{}})
data["axf_id"] = axf_id
base_dir = Path("../file_system/analytics_db")
truth_dir = Path("../file_system/metrics_truth")
base_dir.mkdir(parents=True, exist_ok=True)
truth_dir.mkdir(parents=True, exist_ok=True)
(base_dir / f"{{axf_id}}.json").write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
(truth_dir / f"{{axf_id}}.json").write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
print(f"Pulled {{axf_id}} from dev")
"""
                try:
                    result = subprocess.run(
                        [sys.executable, "-c", code],
                        cwd=cwd,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=90,
                    )
                    if result.returncode == 0:
                        st.success("Pulled from dev.")
                        st.rerun()
                    else:
                        st.error(f"Pull failed: {result.stderr}")
                except subprocess.TimeoutExpired:
                    st.error("Pull timed out.")

        st.markdown("**Push to Dev (single metric)**")
        baseline_choice = st.radio(
            "Diff baseline",
            options=["prod", "dev"],
            index=0,
            horizontal=True,
            key=f"metric_single_baseline_{axf_id}",
        )
        if st.button("Compute diff", key=f"metric_single_diff_{axf_id}", use_container_width=True):
            diff, baseline_src, baseline_ts, err = metric_push_pipeline.compute_single_metric_diff(
                axf_id=axf_id,
                baseline_choice=baseline_choice,
            )
            st.session_state[f"metric_single_diff_data_{axf_id}"] = {
                "diff": diff,
                "baseline_source": baseline_src,
                "baseline_ts": baseline_ts,
                "error": err,
            }

        diff_state = st.session_state.get(f"metric_single_diff_data_{axf_id}") or {}
        diff = diff_state.get("diff")
        diff_err = diff_state.get("error")
        baseline_src = diff_state.get("baseline_source") or baseline_choice
        baseline_ts = diff_state.get("baseline_ts")

        if diff_err:
            st.error(diff_err)
        elif isinstance(diff, dict):
            changed = diff.get("changed_fields") or []
            st.caption(
                f"Diff baseline: {baseline_src}"
                + (f" (last_update_time={baseline_ts})" if baseline_ts is not None else "")
            )
            with st.expander("Differences (baseline → local)", expanded=False):
                for field_name in changed:
                    if not isinstance(field_name, str) or not field_name:
                        continue
                    st.markdown(f"**{field_name}**")
                    lcol, rcol = st.columns(2, gap="large")
                    with lcol:
                        st.caption("Before (baseline)")
                        _render_json_value((diff.get("before") or {}).get(field_name))
                    with rcol:
                        st.caption("After (local)")
                        _render_json_value((diff.get("after") or {}).get(field_name))

            if st.button(
                "Push this metric to dev",
                key=f"metric_single_push_{axf_id}",
                use_container_width=True,
                disabled=(len(changed) == 0),
            ):
                ok, out = metric_push_pipeline.push_metrics_to_dev([axf_id])
                if ok:
                    st.success("Metric push complete (dev).")
                else:
                    st.error(f"Metric push failed.\n\n{out}")

    with right:
        h1, h2 = st.columns([0.78, 0.22], vertical_alignment="center")
        with h1:
            header_slot = st.empty()
        with h2:
            prompt_slot = st.empty()

        if field == "Basics":
            name = st.text_input("Name", value=name)
            description = st.text_area("Description", value=description, height=140)
            units_str = st.text_input("Units", value=units_str, help="Empty = null")
            equation = st.text_area("Equation", value=equation, height=120)
            script = st.text_area("Script", value=script, height=220)

        elif field == "Dependencies":
            rc = st.text_area(
                "Required components (comma/newline separated)",
                value=", ".join(required_components),
                height=90,
            )
            rm = st.text_area(
                "Required metrics (comma/newline separated)",
                value=", ".join(required_metrics),
                height=90,
            )
            rd = st.text_area(
                "Required devices (comma/newline separated)",
                value=", ".join(required_devices),
                height=90,
            )
            required_components = _csv_list(rc)
            required_metrics = _csv_list(rm)
            required_devices = _csv_list(rd)

        elif field == "LaTeX":
            latex_formula_str = st.text_area("LaTeX formula", value=latex_formula_str, height=160)
            if latex_formula_str.strip():
                st.caption("Preview")
                try:
                    st.latex(latex_formula_str.strip())
                except Exception:
                    st.warning("LaTeX preview failed (still editable).")

        elif field == "Capture-type overrides":
            if not capture_types:
                st.caption("No capture types loaded yet. Pull from Firebase first.")
            else:
                cap = st.selectbox("Capture type", options=capture_types)
                current_raw = optimization_mode.get(cap)
                current_norm = normalize_optimization_mode(str(current_raw)) if current_raw is not None else None
                # Preserve exact canonical labels in the dropdown.
                options = [""] + CHOICES
                current_idx = options.index(current_norm) if current_norm in options else 0

                ee = str(equation_explanation.get(cap) or "")
                ci = str(capture_type_info.get(cap) or "")

                om2 = st.selectbox("Optimization mode", options=options, index=current_idx)
                ee2 = st.text_area("Equation explanation", value=ee, height=110)
                ci2 = st.text_area("How to use (capture type info)", value=ci, height=110)

                def _set_or_del(d: dict[str, Any], k: str, v: str) -> None:
                    if v.strip():
                        d[k] = v.strip()
                    else:
                        d.pop(k, None)

                _set_or_del(optimization_mode, cap, om2)
                _set_or_del(equation_explanation, cap, ee2)
                _set_or_del(capture_type_info, cap, ci2)

        else:
            # Raw JSON
            raw = st.text_area("Metric JSON", value=_pretty_json(m), height=520)
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    m = obj
                else:
                    st.error("JSON root must be an object (dict).")
            except Exception as e:
                st.error(f"JSON parse error: {e}")

    # Normalize script escaped sequences into real newlines
    if isinstance(script, str):
        t = script.strip()
        # Common case: user pasted a JSON string literal like "line1\nline2"
        # (including the surrounding quotes).
        if t.startswith('"') and t.endswith('"') and ("\\n" in t or "\\r\\n" in t or "\\t" in t):
            try:
                script = json.loads(t)
            except Exception:
                script = script
        # Also handle the raw-text case where the textarea contains literal backslash
        # sequences (what the user sees as "\n", not an actual newline).
        if "\\r\\n" in script:
            script = script.replace("\\r\\n", "\n")
        if "\\n" in script:
            script = script.replace("\\n", "\n")
        if "\\t" in script:
            script = script.replace("\\t", "\t")

    # Apply updated fields back onto m (unless Raw JSON mode overwrote it).
    # Keep axf_id consistent.
    m["axf_id"] = axf_id
    if field != "Raw JSON (advanced)":
        m["name"] = name.strip()
        m["description"] = description.strip() if description.strip() else None
        m["units"] = units_str.strip() if units_str.strip() else None
        m["equation"] = equation.strip() if equation.strip() else None
        m["script"] = script.strip() if script.strip() else None

        m["required_components"] = required_components
        m["required_metrics"] = required_metrics
        m["required_devices"] = required_devices

        m["latex_formula"] = latex_formula_str.strip() if latex_formula_str.strip() else None
        m["optimization_mode"] = optimization_mode
        m["equation_explanation"] = equation_explanation
        m["capture_type_info"] = capture_type_info

    # Live re-analysis: mark incomplete + update prompt availability immediately as user edits.
    incomplete_now = is_incomplete(m or {})
    header_slot.markdown(f"**Editing** `{axf_id}`" + (" *" if incomplete_now else ""))

    with prompt_slot.container():
        if not incomplete_now:
            st.button(
                "Generate Prompt",
                type="secondary",
                disabled=True,
                help="Enabled when the metric is incomplete (missing required fields).",
                use_container_width=True,
                key=f"gen_prompt_disabled__{axf_id}",
            )
        else:
            # Popover with prompt modes. (No `key=` support on older Streamlit popovers.)
            with st.popover("Generate Prompt", use_container_width=True):
                # Marker element so we can target CSS to widen only this popover.
                st.markdown("<div class='axfPromptPopoverMarker'></div>", unsafe_allow_html=True)
                mode = st.radio(
                    "Mode",
                    options=["Auto", "Choose reference(s)"],
                    index=0,
                    help="Auto picks similar metrics. Choose reference(s) lets you pick one or more metrics and describe differences.",
                )

                if mode == "Choose reference(s)":
                    choices = [c for c in (metric_choices or []) if isinstance(c, str) and c and c != axf_id]
                    if not choices:
                        st.info("No reference metrics available yet.")
                    else:
                        label_map = metric_label_map or {}

                        def _fmt_ref(x: str) -> str:
                            return label_map.get(x, x)

                        ref_axfs = st.multiselect(
                            "Reference metric(s)",
                            options=choices,
                            default=[],
                            key=f"llm_ref_metrics__{axf_id}",
                            format_func=_fmt_ref,
                        )
                        diff = st.text_area(
                            "What’s different?",
                            value="",
                            placeholder=(
                                "Describe the minimal changes needed vs the reference metric(s). "
                                "Be explicit about script semantics (e.g., independent Left/Right peaks vs Parent-aligned peak time)."
                            ),
                            height=110,
                            key=f"llm_ref_diff__{axf_id}",
                        )
                        if st.button(
                            "Copy prompt",
                            type="primary",
                            use_container_width=True,
                            key=f"llm_copy_refs__{axf_id}",
                            disabled=(not ref_axfs),
                        ):
                            st.session_state["_llm_prompt_request"] = {
                                "axf_id": axf_id,
                                "mode": "multi_reference",
                                "reference_axf_ids": list(ref_axfs),
                                "diff_notes": diff,
                            }
                else:
                    st.caption("Auto mode will pick similar metrics for examples.")
                    if st.button("Copy prompt", type="primary", use_container_width=True, key=f"llm_copy_auto__{axf_id}"):
                        st.session_state["_llm_prompt_request"] = {"axf_id": axf_id, "mode": "auto"}
        # No prompt UI here; button copies to clipboard.

    return m

