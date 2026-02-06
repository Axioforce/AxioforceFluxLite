from __future__ import annotations

import json
import re
from typing import Any, Iterable

from tools.MetricsEditor import analytics_index


REQUIRED_NON_NULL_FIELDS: tuple[str, ...] = ("name", "description", "equation", "script", "latex_formula")


# Condensed scripting + wiring rules used in "Generate Prompt". This is intentionally short enough to fit
# in typical LLM contexts, but covers the non-obvious constraints (exec(), result assignment, phase wiring).
CONDENSED_SCRIPTING_REFERENCE = """\
## Runtime scripting contract (condensed)
- Metric scripts are executed via Python `exec()` at top-level. **Never use `return`.**
- You MUST assign the final value to a variable named **`result`**.
- Available inputs:
  - `data`: usually a pandas DataFrame for raw-DataFrame metrics (device analytics / phase analytics / multi-phase analytics);
           for capture-level derived (`analytics_keys`) it may be a dict-of-lists of other metric outputs.
  - `component`: component string like 'Fx'/'Fy'/'Fz'/'Pz'/etc (depends on metric + capture config).
  - `body_mass`: float
  - `np`: numpy
- If validation fails, set `result` to a short error string; do not raise.
- If returning a metric timestamp, use a 2-tuple: `result = (value, time_ms)`.

## Phase-bounded rule (critical)
- A script does NOT "know" phases unless it filters `data` itself.
- **Phase-bounded execution is decided by capture config wiring** (`phases[].phase_analytics_keys`),
  which passes `data` already filtered to that phase.

## Cross-device comparison rule (critical)
- If the script compares Left/Right/Landing Zone/etc, the metric must be configured so the engine passes
  a combined DataFrame with a `position_id` column (typically requires `required_devices` to include `'all'`).

## Capture config wiring vocabulary (what user must configure)
- `device_analytics_keys`: runs once per device over whole capture (raw DataFrame).
- `phases[].phase_analytics_keys`: runs inside specific phases only (raw DataFrame, phase-filtered).
- `multi_phase_analytics_keys`: runs over selected phases combined (raw DataFrame, per device).
- `analytics_keys`: **capture-level derived** metrics (input may be dict-of-lists; NOT raw DataFrame).
- `metric_priority`: UI card ordering + display labels (axis/phase/device); does NOT control compute execution.
"""


def _has_text(v: Any) -> bool:
    return isinstance(v, str) and v.strip() != ""


def is_incomplete(metric: dict[str, Any]) -> bool:
    """
    A metric is considered "incomplete" when any required field is missing/empty.
    """
    for k in REQUIRED_NON_NULL_FIELDS:
        if not _has_text(metric.get(k)):
            return True
    return False


def _metric_text_for_similarity(m: dict[str, Any]) -> str:
    name = str(m.get("name") or "")
    axf_id = str(m.get("axf_id") or "")
    desc = str(m.get("description") or "")
    # Keep it short-ish; tokenization will discard most noise anyway.
    return " ".join([name, axf_id, desc]).strip()


def _token_set(s: str) -> set[str]:
    return set(analytics_index.tokenize(s or ""))


def _jacc(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def select_similar_metrics(
    *,
    current_metric: dict[str, Any],
    candidates: Iterable[dict[str, Any]],
    k: int = 3,
) -> list[dict[str, Any]]:
    """
    Select the top-k most similar metrics (by token overlap).

    `candidates` are expected to be full metric JSON dicts (truth/base).
    """
    cur_ax = str(current_metric.get("axf_id") or "")
    cur_tokens = _token_set(_metric_text_for_similarity(current_metric))

    scored: list[tuple[float, dict[str, Any]]] = []
    for c in candidates:
        if not isinstance(c, dict):
            continue
        ax = c.get("axf_id")
        if not isinstance(ax, str) or not ax:
            continue
        if cur_ax and ax == cur_ax:
            continue

        tokens = _token_set(_metric_text_for_similarity(c))
        score = _jacc(cur_tokens, tokens)
        if score <= 0:
            continue
        scored.append((score, c))

    scored.sort(key=lambda t: t[0], reverse=True)
    return [c for _s, c in scored[: max(0, int(k or 0))]]


def _pretty_json(obj: dict[str, Any]) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def _phase_mentions_for_capture_type(
    *,
    capture_type_id: str,
    current_metric: dict[str, Any],
    wiring_context: dict[str, Any] | None,
) -> list[str]:
    """
    Best-effort: infer phase names the metric text claims to use, per capture type.
    We use the capture type's known phases (from wiring_context) as the vocabulary and
    scan key metric text fields for those exact names.
    """
    if not isinstance(capture_type_id, str) or not capture_type_id:
        return []
    if not isinstance(current_metric, dict):
        return []
    if not isinstance(wiring_context, dict):
        return []

    wired = wiring_context.get("wired_capture_types") if isinstance(wiring_context.get("wired_capture_types"), dict) else {}
    ct = wired.get(capture_type_id) if isinstance(wired.get(capture_type_id), dict) else {}
    phase_names = ct.get("phase_names") if isinstance(ct.get("phase_names"), list) else []
    phase_names = [p for p in phase_names if isinstance(p, str) and p.strip()]
    if not phase_names:
        return []

    # Text sources: broad + capture-type-specific
    eq = str(current_metric.get("equation") or "")
    desc = str(current_metric.get("description") or "")
    ee = current_metric.get("equation_explanation") if isinstance(current_metric.get("equation_explanation"), dict) else {}
    ee_ct = str(ee.get(capture_type_id) or "")

    text = " ".join([eq, desc, ee_ct]).strip().lower()
    hits: list[str] = []
    for pn in phase_names:
        # match as a whole word-ish token
        pat = r"(?i)(?:^|[^a-z0-9_])" + re.escape(pn) + r"(?:$|[^a-z0-9_])"
        if re.search(pat, text):
            hits.append(pn)
    return hits


def build_prompt(
    *,
    current_metric: dict[str, Any],
    similar_metrics: list[dict[str, Any]],
    wiring_context: dict[str, Any] | None = None,
    scripting_reference: str | None = None,
) -> str:
    axf_id = str(current_metric.get("axf_id") or "")

    missing = [k for k in REQUIRED_NON_NULL_FIELDS if not _has_text(current_metric.get(k))]
    missing_txt = ", ".join(missing) if missing else "(none)"

    lines: list[str] = []
    lines.append("### Context")
    lines.append(
        "You are helping fill in a metric JSON for Axioforce FluxDeluxe Metrics Editor. "
        "A metric JSON describes a computed metric and may include per-capture-type overrides."
    )
    lines.append("")

    if isinstance(scripting_reference, str) and scripting_reference.strip():
        lines.append("### Scripting + capture wiring reference (read carefully)")
        lines.append(scripting_reference.strip())
        lines.append("")
    else:
        lines.append("### Scripting + capture wiring reference (condensed)")
        lines.append(CONDENSED_SCRIPTING_REFERENCE.strip())
        lines.append("")

    if isinstance(wiring_context, dict) and wiring_context:
        lines.append("### Observed capture-type wiring context (from repo JSON snapshots)")
        lines.append("This helps you recommend which capture config settings the user should change.")
        lines.append("```json")
        lines.append(_pretty_json(wiring_context))
        lines.append("```")
        lines.append("")

    lines.append("### Current metric (truth JSON so far)")
    lines.append("This is the metric JSON as it exists in the tool right now. Do not change `axf_id`.")
    lines.append("")
    lines.append("```json")
    lines.append(_pretty_json(current_metric))
    lines.append("```")
    lines.append("")

    lines.append("### Consistency contract (MOST IMPORTANT)")
    lines.append(
        "- The existing metric fields (especially `equation`, `description`, and `equation_explanation`) define the intended meaning.\n"
        "- Your `script` MUST implement the same meaning. Do NOT invent a different definition.\n"
        "- If there is any ambiguity, prefer keeping the existing text meaning and update the script to match it.\n"
        "- Only change existing non-empty text fields if they clearly contradict each other; if you do, keep changes minimal and explain why."
    )
    lines.append("")
    lines.append("### Phase/scope lock (CRITICAL — prevents drift)")
    lines.append(
        "- If the current metric text mentions specific phase(s) (e.g. “Propulsive phase only”), treat that as a hard requirement.\n"
        "- Do NOT broaden the metric to additional phases (e.g. adding Braking) unless you ALSO update the defining text fields "
        "(`description`, `equation`, and `equation_explanation`) to match, and explicitly call out the change in justification.\n"
        "- Prefer wiring to the declared phases via capture config (phase-bounded) rather than filtering phases inside the script.\n"
        "- If the metric is wired phase-bounded to a single phase, the script should generally assume `data` is already phase-filtered "
        "and should not add extra phase filters."
    )
    lines.append("")

    if similar_metrics:
        lines.append("### Similar metrics (full JSON examples)")
        lines.append("Use these to match style, level of detail, and field conventions.")
        lines.append("")
        for i, ex in enumerate(similar_metrics, start=1):
            ex_name = str(ex.get("name") or "")
            ex_ax = str(ex.get("axf_id") or "")
            label = f"Example {i}: {ex_name} ({ex_ax})".strip()
            lines.append(f"#### {label}")
            lines.append("```json")
            lines.append(_pretty_json(ex))
            lines.append("```")
            lines.append("")

    lines.append("### Task")
    lines.append(f"- Produce an updated, complete metric JSON for `axf_id`: `{axf_id}`.")
    lines.append(f"- Required non-null fields to fill: {', '.join(REQUIRED_NON_NULL_FIELDS)}.")
    lines.append(f"- Fields currently missing/empty (priority): {missing_txt}.")
    lines.append("- You will output values for ALL fields (including ones that can be `null`).")
    lines.append("- If something is unknown, set it to `null` or an empty list/dict as appropriate, but the required fields above must be non-null, meaningful strings.")
    lines.append("- Keep any existing keys you see in the current JSON; do not delete keys.")
    lines.append("")
    lines.append("- ALSO: recommend the capture config wiring settings for this metric (what to toggle, and which phases/devices).")
    lines.append("")
    lines.append("### Rules")
    lines.append("- Keep `axf_id` EXACTLY the same.")
    lines.append("- `optimization_mode` values must be one of: `Maximize`, `Minimize`, `Abs Maximize`, `Abs Minimize`, `Target`, or `null`/empty.")
    lines.append("#### Field guidelines")
    lines.append("- `name`: short Title Case display name used in UI.")
    lines.append("- `description`: 1–2 coach-friendly sentences describing what the metric measures (NOT capture-type-specific).")
    lines.append("  - Capture-type-specific usage notes belong in `capture_type_info[capture_type_id]` instead.")
    lines.append("- `equation`: a readable equation string (not necessarily executable) consistent with `script`.")
    lines.append("- `units`: short unit string like `cm`, `Ns`, `kg*m/s`, or `null` if unknown.")
    lines.append("- `required_components`: list of force components required (e.g. `['Fx','Fy','Fz','vector']`) or empty list.")
    lines.append("- `required_devices`: list like `['Parent']` or empty list.")
    lines.append("- `required_metrics`: other axf_id dependencies, or empty list.")
    lines.append("- `latex_formula`: LaTeX math string for the equation (no surrounding `\\[ \\]`).")
    lines.append("- `optimization_mode`, `equation_explanation`, `capture_type_info`: per-capture-type dicts; keep keys as capture type ids; values strings or omitted/empty where unknown.")
    lines.append("")
    lines.append("#### `script` rules (IMPORTANT)")
    lines.append("- The script MUST assign the final output to a variable named `result` (never `return`).")
    lines.append("- It MUST be self-contained: no external helper function calls; only use provided inputs/locals.")
    lines.append("- It MUST match the meaning of `equation` + `equation_explanation` (see Consistency contract).")
    lines.append("- It MUST use simple control flow; preferred pattern when validation is needed:")
    lines.append("```python")
    lines.append("result = None")
    lines.append("while result is None:")
    lines.append("    if data.empty:")
    lines.append("        result = 'No data found'")
    lines.append("        break")
    lines.append("    # ... more validations ...")
    lines.append("    result = <final_value_or_tuple>")
    lines.append("    break")
    lines.append("```")
    lines.append("- If the metric is a simple computation, it's fine to be concise, e.g.:")
    lines.append("```python")
    lines.append("peak_force_idx = data[component].abs().idxmax()")
    lines.append("result = (data.at[peak_force_idx, component], data.at[peak_force_idx, 'time'])")
    lines.append("```")
    lines.append("- When failing validation, set `result` to a short error string (do not raise).")
    lines.append("- Prefer concise, coach-friendly `description` and a clear, implementable `equation`.")
    lines.append("")
    lines.append("### Response format (MUST follow exactly)")
    lines.append("Output TWO sections in this exact order:")
    lines.append("")
    lines.append("## Section A — Metric JSON fields")
    lines.append("List ALL fields you see in the current JSON using this exact style:")
    lines.append("Field: <value>")
    lines.append("Justification: <optional, 0–2 sentences>")
    lines.append("")
    lines.append("Notes (Section A):")
    lines.append("- Use the exact JSON key names for `Field` (e.g., `name`, `description`, `units`, `required_components`, `optimization_mode`).")
    lines.append("- For dict/list fields, use valid JSON inline (single line if possible).")
    lines.append("- Required fields must not be null/empty; other fields may be `null`.")
    lines.append("- Do NOT wrap the output in ``` fences.")
    lines.append("")
    lines.append("## Section B — Capture config wiring recommendations")
    lines.append("Explain what the user should set in capture configs for this metric.")
    lines.append("- If phase-bounded: specify the exact phase name(s) to wire under `phases[].phase_analytics_keys`.")
    lines.append("- If capture-wide: specify `device_analytics_keys`.")
    lines.append("- If multi-phase: specify a `multi_phase_analytics_keys` entry (phase_names + data_set_devices).")
    lines.append("- If derived: specify `analytics_keys` and required_metrics inputs.")
    lines.append("- If it should be a key UI metric: specify a `metric_priority` card suggestion (axis/phase/device labels).")
    lines.append("- IMPORTANT: Do not put raw-DataFrame metrics under `analytics_keys` unless it is truly derived from other metrics.")
    lines.append("")

    return "\n".join(lines)


def build_single_reference_prompt(
    *,
    current_metric: dict[str, Any],
    reference_metric: dict[str, Any],
    diff_notes: str,
    wiring_context: dict[str, Any] | None = None,
    scripting_reference: str | None = None,
) -> str:
    """
    Prompt framed as: "make current metric like reference metric, with minimal changes".
    """
    axf_id = str(current_metric.get("axf_id") or "")
    ref_axf = str(reference_metric.get("axf_id") or "")
    ref_name = str(reference_metric.get("name") or "")

    missing = [k for k in REQUIRED_NON_NULL_FIELDS if not _has_text(current_metric.get(k))]
    missing_txt = ", ".join(missing) if missing else "(none)"

    lines: list[str] = []
    lines.append("### Context")
    lines.append(
        "You are helping fill in a metric JSON for Axioforce FluxDeluxe Metrics Editor. "
        "We want the NEW/INCOMPLETE metric to be extremely close to a single chosen reference metric."
    )
    lines.append("")

    if isinstance(scripting_reference, str) and scripting_reference.strip():
        lines.append("### Scripting + capture wiring reference (read carefully)")
        lines.append(scripting_reference.strip())
        lines.append("")
    else:
        lines.append("### Scripting + capture wiring reference (condensed)")
        lines.append(CONDENSED_SCRIPTING_REFERENCE.strip())
        lines.append("")

    if isinstance(wiring_context, dict) and wiring_context:
        lines.append("### Observed capture-type wiring context (from repo JSON snapshots)")
        lines.append("```json")
        lines.append(_pretty_json(wiring_context))
        lines.append("```")
        lines.append("")

    lines.append("### Current metric (truth JSON so far)")
    lines.append("Do not change `axf_id`. You will output values for ALL fields.")
    lines.append("```json")
    lines.append(_pretty_json(current_metric))
    lines.append("```")
    lines.append("")

    lines.append("### Consistency contract (MOST IMPORTANT)")
    lines.append(
        "- The existing metric fields in the current JSON (especially `equation`, `description`, and `equation_explanation`) define the intended meaning.\n"
        "- Your `script` MUST implement the same meaning.\n"
        "- Even though you will copy structure from the reference metric, do NOT copy its semantics if they conflict with the current metric’s meaning.\n"
        "- Prefer aligning the script to the current metric meaning; only adjust existing text fields if they clearly contradict each other, and explain."
    )
    lines.append("")
    lines.append("### Phase/scope lock (CRITICAL — prevents drift)")
    lines.append(
        "- If the current metric text mentions specific phase(s) (e.g. “Propulsive phase only”), treat that as a hard requirement.\n"
        "- Do NOT broaden the metric to additional phases (e.g. adding Braking) unless you ALSO update the defining text fields "
        "(`description`, `equation`, and `equation_explanation`) to match, and explicitly call out the change.\n"
        "- Prefer wiring to the declared phases via capture config rather than filtering phases inside the script.\n"
        "- If phase-bounded wiring is selected, assume `data` already contains only that phase."
    )
    lines.append("")

    lines.append(f"### Reference metric (full JSON): {ref_name} ({ref_axf})")
    lines.append("Use this as the template for style, wording, and especially the `script` structure.")
    lines.append("```json")
    lines.append(_pretty_json(reference_metric))
    lines.append("```")
    lines.append("")

    if isinstance(diff_notes, str) and diff_notes.strip():
        lines.append("### Differences (user notes)")
        lines.append(diff_notes.strip())
        lines.append("")

    lines.append("### Task")
    lines.append(f"- Produce an updated, complete metric JSON for `axf_id`: `{axf_id}`.")
    lines.append(f"- Required non-null fields to fill: {', '.join(REQUIRED_NON_NULL_FIELDS)}.")
    lines.append(f"- Fields currently missing/empty (priority): {missing_txt}.")
    lines.append("- Make the result as close as possible to the reference metric, changing only what is necessary.")
    lines.append("")
    lines.append("- ALSO: recommend the capture config wiring settings for this metric (what to toggle, and which phases/devices).")
    lines.append("")

    lines.append("### Rules")
    lines.append("- Keep `axf_id` EXACTLY the same.")
    lines.append("- `description` must be general (NOT capture-type-specific).")
    lines.append("- `optimization_mode` values must be one of: `Maximize`, `Minimize`, `Abs Maximize`, `Abs Minimize`, `Target`, or `null`/empty.")
    lines.append("")
    lines.append("#### `script` rules (MOST IMPORTANT)")
    lines.append("- Keep the script structure extremely similar to the reference metric.")
    lines.append("- The script MUST assign the final output to a variable named `result` (never `return`).")
    lines.append("- It MUST be self-contained: no external helper function calls; only use provided inputs/locals.")
    lines.append("- It MUST match the meaning of the current metric’s `equation` + `equation_explanation` (see Consistency contract).")
    lines.append("- When failing validation, set `result` to a short error string (do not raise).")
    lines.append("")

    lines.append("### Response format (MUST follow exactly)")
    lines.append("Output TWO sections in this exact order:")
    lines.append("")
    lines.append("## Section A — Metric JSON fields")
    lines.append("List ALL fields you see in the current JSON using this exact style:")
    lines.append("Field: <value>")
    lines.append("Justification: <optional, 0–2 sentences>")
    lines.append("")
    lines.append("Notes (Section A):")
    lines.append("- Use the exact JSON key names for `Field`.")
    lines.append("- For dict/list fields, use valid JSON inline (single line if possible).")
    lines.append("- Required fields must not be null/empty; other fields may be `null`.")
    lines.append("- Do NOT wrap the output in ``` fences.")
    lines.append("")
    lines.append("## Section B — Capture config wiring recommendations")
    lines.append("Explain what the user should set in capture configs for this metric.")
    lines.append("- If phase-bounded: specify the exact phase name(s) to wire under `phases[].phase_analytics_keys`.")
    lines.append("- If capture-wide: specify `device_analytics_keys`.")
    lines.append("- If multi-phase: specify a `multi_phase_analytics_keys` entry (phase_names + data_set_devices).")
    lines.append("- If derived: specify `analytics_keys` and required_metrics inputs.")
    lines.append("- If it should be a key UI metric: specify a `metric_priority` card suggestion (axis/phase/device labels).")
    lines.append("- IMPORTANT: Do not put raw-DataFrame metrics under `analytics_keys` unless it is truly derived from other metrics.")
    lines.append("")

    return "\n".join(lines)


def build_multi_reference_prompt(
    *,
    current_metric: dict[str, Any],
    reference_metrics: list[dict[str, Any]],
    diff_notes: str,
    wiring_context: dict[str, Any] | None = None,
    scripting_reference: str | None = None,
) -> str:
    """
    Prompt framed as: "make current metric consistent, using multiple references for examples".
    """
    axf_id = str(current_metric.get("axf_id") or "")

    missing = [k for k in REQUIRED_NON_NULL_FIELDS if not _has_text(current_metric.get(k))]
    missing_txt = ", ".join(missing) if missing else "(none)"

    lines: list[str] = []
    lines.append("### Context")
    lines.append(
        "You are helping fill in a metric JSON for Axioforce FluxDeluxe Metrics Editor. "
        "We want the NEW/INCOMPLETE metric to be consistent with its existing meaning, and we provide multiple "
        "reference metrics as style/structure examples."
    )
    lines.append("")

    if isinstance(scripting_reference, str) and scripting_reference.strip():
        lines.append("### Scripting + capture wiring reference (read carefully)")
        lines.append(scripting_reference.strip())
        lines.append("")
    else:
        lines.append("### Scripting + capture wiring reference (condensed)")
        lines.append(CONDENSED_SCRIPTING_REFERENCE.strip())
        lines.append("")

    if isinstance(wiring_context, dict) and wiring_context:
        lines.append("### Observed capture-type wiring context (from repo JSON snapshots)")
        lines.append("```json")
        lines.append(_pretty_json(wiring_context))
        lines.append("```")
        lines.append("")

    lines.append("### Current metric (truth JSON so far)")
    lines.append("Do not change `axf_id`. You will output values for ALL fields.")
    lines.append("```json")
    lines.append(_pretty_json(current_metric))
    lines.append("```")
    lines.append("")

    lines.append("### Consistency contract (MOST IMPORTANT)")
    lines.append(
        "- The existing metric fields in the current JSON (especially `equation`, `description`, and `equation_explanation`) define the intended meaning.\n"
        "- Your `script` MUST implement the same meaning.\n"
        "- Use the reference metrics only for structure/style/patterns; do NOT copy semantics that conflict with the current metric’s meaning.\n"
        "- Prefer aligning the script to the current metric meaning; only adjust existing text fields if they clearly contradict each other, and explain."
    )
    lines.append("")
    lines.append("### Phase/scope lock (CRITICAL — prevents drift)")
    lines.append(
        "- If the current metric text mentions specific phase(s) (e.g. “Propulsive phase only”), treat that as a hard requirement.\n"
        "- Do NOT broaden the metric to additional phases (e.g. adding Braking) unless you ALSO update the defining text fields "
        "(`description`, `equation`, and `equation_explanation`) to match, and explicitly call out the change.\n"
        "- Prefer wiring to the declared phases via capture config rather than filtering phases inside the script.\n"
        "- If phase-bounded wiring is selected, assume `data` already contains only that phase."
    )
    lines.append("")

    if reference_metrics:
        lines.append("### Reference metrics (full JSON examples)")
        lines.append("Use these to match style and script patterns. Do NOT copy semantics if they conflict with the current metric meaning.")
        lines.append("")
        for i, ex in enumerate(reference_metrics, start=1):
            ex_name = str(ex.get("name") or "")
            ex_ax = str(ex.get("axf_id") or "")
            label = f"Reference {i}: {ex_name} ({ex_ax})".strip()
            lines.append(f"#### {label}")
            lines.append("```json")
            lines.append(_pretty_json(ex))
            lines.append("```")
            lines.append("")

    if isinstance(diff_notes, str) and diff_notes.strip():
        lines.append("### Differences (user notes)")
        lines.append(diff_notes.strip())
        lines.append("")

    lines.append("### Task")
    lines.append(f"- Produce an updated, complete metric JSON for `axf_id`: `{axf_id}`.")
    lines.append(f"- Required non-null fields to fill: {', '.join(REQUIRED_NON_NULL_FIELDS)}.")
    lines.append(f"- Fields currently missing/empty (priority): {missing_txt}.")
    lines.append("- Use the references for patterns, but keep the meaning consistent with the current metric fields.")
    lines.append("")
    lines.append("- ALSO: recommend the capture config wiring settings for this metric (what to toggle, and which phases/devices).")
    lines.append("")

    lines.append("### Rules")
    lines.append("- Keep `axf_id` EXACTLY the same.")
    lines.append("- `description` must be general (NOT capture-type-specific).")
    lines.append("- `optimization_mode` values must be one of: `Maximize`, `Minimize`, `Abs Maximize`, `Abs Minimize`, `Target`, or `null`/empty.")
    lines.append("")
    lines.append("#### `script` rules (MOST IMPORTANT)")
    lines.append("- The script MUST assign the final output to a variable named `result` (never `return`).")
    lines.append("- It MUST be self-contained: no external helper function calls; only use provided inputs/locals.")
    lines.append("- It MUST match the meaning of the current metric’s `equation` + `equation_explanation` (see Consistency contract).")
    lines.append("- When failing validation, set `result` to a short error string (do not raise).")
    lines.append("")

    lines.append("### Response format (MUST follow exactly)")
    lines.append("Output TWO sections in this exact order:")
    lines.append("")
    lines.append("## Section A — Metric JSON fields")
    lines.append("List ALL fields you see in the current JSON using this exact style:")
    lines.append("Field: <value>")
    lines.append("Justification: <optional, 0–2 sentences>")
    lines.append("")
    lines.append("Notes (Section A):")
    lines.append("- Use the exact JSON key names for `Field`.")
    lines.append("- For dict/list fields, use valid JSON inline (single line if possible).")
    lines.append("- Required fields must not be null/empty; other fields may be `null`.")
    lines.append("- Do NOT wrap the output in ``` fences.")
    lines.append("")
    lines.append("## Section B — Capture config wiring recommendations")
    lines.append("Explain what the user should set in capture configs for this metric.")
    lines.append("- If phase-bounded: specify the exact phase name(s) to wire under `phases[].phase_analytics_keys`.")
    lines.append("- If capture-wide: specify `device_analytics_keys`.")
    lines.append("- If multi-phase: specify a `multi_phase_analytics_keys` entry (phase_names + data_set_devices).")
    lines.append("- If derived: specify `analytics_keys` and required_metrics inputs.")
    lines.append("- If it should be a key UI metric: specify a `metric_priority` card suggestion (axis/phase/device labels).")
    lines.append("- IMPORTANT: Do not put raw-DataFrame metrics under `analytics_keys` unless it is truly derived from other metrics.")
    lines.append("")

    return "\n".join(lines)

