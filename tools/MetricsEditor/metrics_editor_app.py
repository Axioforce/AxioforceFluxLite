from __future__ import annotations

import json
import os
import subprocess
import sys
import base64
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from tools.MetricsEditor import (
    analytics_index,
    capture_analytic_settings_pipeline,
    capture_config_push_pipeline,
    metric_push_pipeline,
    docx_ingest,
    latex_ingest,
    file_ingest_state,
    llm_prompt,
    metric_create,
    manual_mapping,
    paths,
    refresh_sources,
    truth_store,
)
from tools.MetricsEditor.normalization import normalize_optimization_mode
from tools.MetricsEditor.ui.capture_type_editor import render_capture_type_editor
from tools.MetricsEditor.ui.metric_form import render_metric_form


APP_TITLE = "Metrics Editor"

# Bump this to force recomputing ingest state when matching logic changes.
INGEST_VERSION = 4


def _copy_to_clipboard(text: str) -> tuple[bool, str]:
    """
    Best-effort OS clipboard copy (local tool).

    Browser/Streamlit clipboard writes are often blocked because they don't happen in the
    same user-gesture event. Since this is a local operator tool, use the OS clipboard.
    """
    t = (text or "")
    if not t.strip():
        return False, "Nothing to copy."

    try:
        if sys.platform.startswith("win"):
            # Prefer PowerShell Set-Clipboard for reliable Unicode handling.
            # This also avoids PATH issues where `clip` might not resolve in some envs.
            try:
                cp = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value ([Console]::In.ReadToEnd())"],
                    input=t,
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    check=False,
                )
                if cp.returncode == 0:
                    return True, "Copied to clipboard."
            except FileNotFoundError:
                cp = None

            # Fallback: clip.exe (best-effort). Use explicit path when possible.
            windir = os.environ.get("WINDIR") or r"C:\Windows"
            clip_exe = Path(windir) / "System32" / "clip.exe"
            cmd = [str(clip_exe)] if clip_exe.exists() else ["clip"]
            cp2 = subprocess.run(cmd, input=t.encode("utf-16le"), text=False, capture_output=True, check=False)
            if cp2.returncode == 0:
                return True, "Copied to clipboard."
            err = ""
            try:
                err = (cp2.stderr or b"").decode("utf-8", errors="ignore").strip()
            except Exception:
                err = ""
            return False, f"Clipboard copy failed (clip.exe). {err}".strip()
        if sys.platform == "darwin":
            cp = subprocess.run(["pbcopy"], input=t, text=True, capture_output=True, check=False)
            return (cp.returncode == 0), ("Copied to clipboard." if cp.returncode == 0 else "Clipboard copy failed (pbcopy).")
        # linux (best-effort)
        cp = subprocess.run(["xclip", "-selection", "clipboard"], input=t, text=True, capture_output=True, check=False)
        return (cp.returncode == 0), ("Copied to clipboard." if cp.returncode == 0 else "Clipboard copy failed (xclip).")
    except FileNotFoundError:
        return False, "Clipboard helper not found on this OS."
    except Exception as e:
        return False, f"Clipboard copy failed: {e}"


def _zw_unique(s: str) -> str:
    """
    Encode a visible string into an invisible, deterministic suffix for widget labels.
    Uses only zero-width characters so UI text stays clean, while Streamlit still sees
    a unique label string.
    """
    bits: list[str] = []
    for ch in (s or ""):
        b = ord(ch)
        # 8 bits per character is enough for ASCII-ish IDs we use here.
        for i in range(7, -1, -1):
            bits.append("\u200b" if ((b >> i) & 1) == 0 else "\u200c")
    # Leading marker to reduce accidental collisions.
    return "\u200d" + "".join(bits)


def _browser_copy_button(*, text: str, button_label: str, element_id: str) -> None:
    """
    Render a browser-side copy button (reliable because it runs on a direct user click).
    Falls back to selecting a textarea if clipboard API is blocked.
    """
    payload = base64.b64encode((text or "").encode("utf-8")).decode("ascii")
    safe_id = "".join([ch for ch in (element_id or "copy") if ch.isalnum() or ch in ("_", "-")]) or "copy"
    html = f"""
<div style="display:flex; gap:8px; align-items:center;">
  <button id="{safe_id}_btn" style="padding:6px 10px; border-radius:8px; border:1px solid rgba(255,255,255,0.15); background:rgba(255,255,255,0.06); color:inherit; cursor:pointer;">
    {button_label}
  </button>
  <span id="{safe_id}_msg" style="opacity:0.75; font-size:12px;"></span>
</div>
<textarea id="{safe_id}_ta" style="position:absolute; left:-9999px; top:-9999px;">{payload}</textarea>
<script>
(function() {{
  const btn = document.getElementById("{safe_id}_btn");
  const msg = document.getElementById("{safe_id}_msg");
  const ta = document.getElementById("{safe_id}_ta");
  function decode() {{
    try {{
      const b64 = ta.value || "";
      const bin = atob(b64);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      return new TextDecoder("utf-8").decode(bytes);
    }} catch (e) {{
      try {{ return atob(ta.value || ""); }} catch (e2) {{ return ""; }}
    }}
  }}
  async function doCopy() {{
    const text = decode();
    if (!text) {{
      msg.textContent = "Nothing to copy.";
      return;
    }}
    try {{
      if (navigator.clipboard && navigator.clipboard.writeText) {{
        await navigator.clipboard.writeText(text);
        msg.textContent = "Copied.";
        return;
      }}
    }} catch (e) {{
      // fall through
    }}
    // Fallback: select + execCommand
    try {{
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.left = "0";
      ta.style.top = "0";
      ta.style.width = "1px";
      ta.style.height = "1px";
      ta.style.opacity = "0";
      ta.focus();
      ta.select();
      const ok = document.execCommand("copy");
      msg.textContent = ok ? "Copied." : "Copy blocked — select text manually below.";
      // keep it hidden again
      ta.style.position = "absolute";
      ta.style.left = "-9999px";
      ta.style.top = "-9999px";
    }} catch (e) {{
      msg.textContent = "Copy blocked — select text manually below.";
    }}
  }}
  if (btn) btn.addEventListener("click", doCopy);
}})();
</script>
"""
    components.html(html, height=48)


def _ensure_dirs() -> None:
    paths.analytics_db_dir().mkdir(parents=True, exist_ok=True)
    paths.capture_config_db_dir().mkdir(parents=True, exist_ok=True)
    paths.truth_dir().mkdir(parents=True, exist_ok=True)
    paths.uploads_docs_dir().mkdir(parents=True, exist_ok=True)
    paths.uploads_latex_dir().mkdir(parents=True, exist_ok=True)


def _list_capture_types_from_snapshots() -> list[str]:
    if not paths.capture_config_db_dir().exists():
        return []
    return sorted([p.stem for p in paths.capture_config_db_dir().glob("*.json")])


def _list_metrics_from_snapshots() -> list[dict[str, Any]]:
    return analytics_index.load_all_base_metrics()


def _pretty_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def _try_parse_metric_json(text: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        obj = json.loads(text)
        if not isinstance(obj, dict):
            return None, "JSON root must be an object (dict)."
        return obj, None
    except Exception as e:
        return None, str(e)


def _save_uploaded_file(uploaded, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_path = dest_dir / uploaded.name
    out_path.write_bytes(uploaded.getvalue())
    return out_path


def _list_files(dir_path: Path, suffixes: tuple[str, ...]) -> list[Path]:
    try:
        if not dir_path.exists():
            return []
        files = [p for p in dir_path.iterdir() if p.is_file() and p.suffix.lower() in suffixes]
        return sorted(files, key=lambda p: p.name.lower())
    except Exception:
        return []


def _delete_file_safely(path: Path, allowed_parent: Path) -> tuple[bool, str]:
    try:
        allowed_parent = allowed_parent.resolve()
        target = path.resolve()
        if allowed_parent not in target.parents:
            return False, "Refusing to delete: path is outside expected folder."
        os.remove(target)
        return True, f"Deleted `{path.name}`"
    except FileNotFoundError:
        return False, "File not found."
    except Exception as e:
        return False, str(e)


def _safe_rename_into_dir(path: Path, target_stem: str, allowed_parent: Path) -> Path:
    """
    Rename a file to <target_stem><suffix> inside allowed_parent, avoiding collisions.
    """
    allowed_parent = allowed_parent.resolve()
    src = path.resolve()
    if allowed_parent not in src.parents:
        raise ValueError("Refusing to rename outside expected folder.")

    suffix = src.suffix
    base = allowed_parent / f"{target_stem}{suffix}"
    if base.resolve() == src:
        return src
    if not base.exists():
        src.rename(base)
        return base

    i = 2
    while True:
        cand = allowed_parent / f"{target_stem}_{i}{suffix}"
        if not cand.exists():
            src.rename(cand)
            return cand
        i += 1


def _infer_capture_type_from_title(title: str, capture_types: list[str]) -> str | None:
    """Best-effort capture type guess from the first line/title in a DOCX.

    We only return a suggestion when we have reasonable confidence.
    """
    title_tokens = set(analytics_index.tokenize(title))
    if not title_tokens:
        return None

    best: tuple[float, str] | None = None
    runner_up: tuple[float, str] | None = None

    for ct in capture_types:
        ct_tokens = set(analytics_index.tokenize(ct))
        if not ct_tokens:
            continue
        score = len(title_tokens & ct_tokens) / len(title_tokens | ct_tokens)
        if best is None or score > best[0]:
            runner_up = best
            best = (score, ct)
        elif runner_up is None or score > runner_up[0]:
            runner_up = (score, ct)

    # Confidence gates: require decent overlap and separation from runner-up.
    if best is None or best[0] < 0.35:
        return None
    if runner_up is not None and (best[0] - runner_up[0]) < 0.10:
        return None
    return best[1]


def _docx_ingest_and_update_state(
    doc_path: Path,
    metric_index: analytics_index.MetricIndex,
    capture_types: list[str],
    state: dict[str, Any],
) -> dict[str, Any]:
    """
    Ingest a single DOCX, update truth JSONs where possible, and write ingest state.
    Returns the updated record.
    """
    filename = doc_path.name
    record = file_ingest_state.get_file_record(state, "docx", filename) or {}
    record.setdefault("manual_map", {})

    lines = docx_ingest.extract_docx_lines(str(doc_path))
    blocks = docx_ingest.parse_metric_blocks_from_lines(lines)

    title = lines[0].strip() if lines else ""
    record["title"] = title

    # capture type suggestion only (user confirms on first mapping)
    if not record.get("capture_type_id") and not record.get("suggested_capture_type_id"):
        inferred = _infer_capture_type_from_title(title, capture_types)
        if inferred:
            record["suggested_capture_type_id"] = inferred

    manual_map = record.get("manual_map") if isinstance(record.get("manual_map"), dict) else {}

    unresolved: list[dict[str, Any]] = []
    updated_metrics: set[str] = set()
    resolved_axf_ids: set[str] = set()
    resolved_items: list[dict[str, Any]] = []

    for b in blocks:
        axf_id, warn = analytics_index.resolve_metric_axf_id(b.name, metric_index, manual_map=manual_map)
        needs_mapping = (axf_id is None) or (warn in ("ambiguous_name", "ambiguous_fuzzy", "no_match"))
        if needs_mapping:
            unresolved.append(
                {
                    "doc_name": b.name,
                    "sig": manual_mapping.key_for_name(b.name),
                    "reason": warn or "no_match",
                    "suggested_axf_id": axf_id,
                    "doc_fields": {
                        "how_to_use": b.how_to_use,
                        "optimization_mode": b.optimization_mode,
                        "equation_explanation": b.equation_explanation,
                    },
                }
            )
            continue

        resolved_items.append(
            {
                "doc_name": b.name,
                "sig": manual_mapping.key_for_name(b.name),
                "reason": warn or "",
                "suggested_axf_id": axf_id,
                "doc_fields": {
                    "how_to_use": b.how_to_use,
                    "optimization_mode": b.optimization_mode,
                    "equation_explanation": b.equation_explanation,
                },
            }
        )

        # Track resolved IDs even if we can't apply to truth yet (e.g. capture type not confirmed).
        if isinstance(axf_id, str) and axf_id:
            resolved_axf_ids.add(axf_id)

        # Only apply to truth if capture type is known
        cap = record.get("capture_type_id")
        if not isinstance(cap, str) or not cap.strip():
            continue

        metric = truth_store.load_truth_or_base(axf_id)
        if b.optimization_mode:
            norm = normalize_optimization_mode(b.optimization_mode)
            if norm is None:
                metric["optimization_mode"].pop(cap, None)
            else:
                metric["optimization_mode"][cap] = norm
        if b.equation_explanation:
            metric["equation_explanation"][cap] = b.equation_explanation
        if b.how_to_use:
            metric["capture_type_info"][cap] = b.how_to_use
        truth_store.save_truth(axf_id, metric)
        updated_metrics.add(axf_id)

    record["total_items"] = len(blocks)
    record["unresolved"] = unresolved
    record["updated_metrics"] = sorted(updated_metrics)
    record["resolved_axf_ids"] = sorted(resolved_axf_ids)
    record["resolved_items"] = resolved_items
    record["mtime"] = doc_path.stat().st_mtime
    record["ingest_version"] = INGEST_VERSION

    file_ingest_state.upsert_file_record(state, "docx", filename, record)
    return record


def _latex_ingest_and_update_state(
    tex_path: Path,
    metric_index: analytics_index.MetricIndex,
    state: dict[str, Any],
) -> dict[str, Any]:
    filename = tex_path.name
    record = file_ingest_state.get_file_record(state, "latex", filename) or {}
    record.setdefault("manual_map", {})

    tex = tex_path.read_text(encoding="utf-8", errors="ignore")
    declared = latex_ingest.parse_declaremetric_blocks(tex)

    manual_map = record.get("manual_map") if isinstance(record.get("manual_map"), dict) else {}

    unresolved: list[dict[str, Any]] = []
    updated_metrics: set[str] = set()
    resolved_axf_ids: set[str] = set()
    resolved_items: list[dict[str, Any]] = []

    for d in declared:
        axf_id, warn = analytics_index.resolve_metric_axf_id(d.name, metric_index, manual_map=manual_map)
        needs_mapping = (axf_id is None) or (warn in ("ambiguous_name", "ambiguous_fuzzy", "no_match"))
        if needs_mapping:
            unresolved.append(
                {
                    "doc_name": d.name,
                    "sig": manual_mapping.key_for_name(d.name),
                    "reason": warn or "no_match",
                    "suggested_axf_id": axf_id,
                    "doc_fields": {
                        "units": d.units,
                        "equation": d.equation,
                        "description": d.description,
                        "how_to_use": d.how_to_use,
                    },
                }
            )
            continue

        resolved_items.append(
            {
                "doc_name": d.name,
                "sig": manual_mapping.key_for_name(d.name),
                "reason": warn or "",
                "suggested_axf_id": axf_id,
                "doc_fields": {
                    "units": d.units,
                    "equation": d.equation,
                    "description": d.description,
                    "how_to_use": d.how_to_use,
                },
            }
        )

        if isinstance(axf_id, str) and axf_id:
            resolved_axf_ids.add(axf_id)

        metric = truth_store.load_truth_or_base(axf_id)
        metric["latex_formula"] = d.equation
        truth_store.save_truth(axf_id, metric)
        updated_metrics.add(axf_id)

    record["total_items"] = len(declared)
    record["unresolved"] = unresolved
    record["updated_metrics"] = sorted(updated_metrics)
    record["resolved_axf_ids"] = sorted(resolved_axf_ids)
    record["resolved_items"] = resolved_items
    record["mtime"] = tex_path.stat().st_mtime
    record["ingest_version"] = INGEST_VERSION

    file_ingest_state.upsert_file_record(state, "latex", filename, record)
    return record


def _open_mapping_dialog(kind: str, filename: str) -> None:
    st.session_state.mapping_dialog = {"kind": kind, "filename": filename, "idx": 0}


def _set_query_map(kind: str, filename: str) -> None:
    st.query_params["map_kind"] = kind
    st.query_params["map_file"] = filename


def _consume_query_map() -> tuple[str | None, str | None]:
    kind = st.query_params.get("map_kind")
    file_ = st.query_params.get("map_file")
    if isinstance(kind, str) and isinstance(file_, str) and kind and file_:
        try:
            st.query_params.pop("map_kind", None)
            st.query_params.pop("map_file", None)
        except Exception:
            pass
        return kind, file_
    return None, None


def _merge_docx_into_truth(
    doc_path: Path,
    capture_type_id: str,
    metric_index: analytics_index.MetricIndex,
    manual_map: dict[str, str] | None,
) -> truth_store.MergeResult:
    lines = docx_ingest.extract_docx_lines(str(doc_path))
    blocks = docx_ingest.parse_metric_blocks_from_lines(lines)

    updated: set[str] = set()
    skipped: dict[str, str] = {}
    for b in blocks:
        axf_id, warn = analytics_index.resolve_metric_axf_id(b.name, metric_index, manual_map=manual_map)
        if not axf_id:
            skipped[b.name] = "no_matching_metric_in_analytics_db"
            continue
        if warn:
            # keep it, but record that we guessed
            skipped[b.name] = warn

        metric = truth_store.load_truth_or_base(axf_id)

        if b.optimization_mode:
            metric["optimization_mode"][capture_type_id] = b.optimization_mode
        if b.equation_explanation:
            metric["equation_explanation"][capture_type_id] = b.equation_explanation
        if b.how_to_use:
            metric["capture_type_info"][capture_type_id] = b.how_to_use

        truth_store.save_truth(axf_id, metric)
        updated.add(axf_id)

    return truth_store.MergeResult(updated_metrics=updated, skipped_metrics=skipped)


def _merge_latex_into_truth(
    tex_path: Path,
    metric_index: analytics_index.MetricIndex,
    manual_map: dict[str, str] | None,
) -> truth_store.MergeResult:
    tex = tex_path.read_text(encoding="utf-8", errors="ignore")
    declared = latex_ingest.parse_declaremetric_blocks(tex)

    updated: set[str] = set()
    skipped: dict[str, str] = {}
    for d in declared:
        axf_id, warn = analytics_index.resolve_metric_axf_id(d.name, metric_index, manual_map=manual_map)
        if not axf_id:
            skipped[d.name] = "no_matching_metric_in_analytics_db"
            continue
        if warn:
            skipped[d.name] = warn

        metric = truth_store.load_truth_or_base(axf_id)
        metric["latex_formula"] = d.equation
        truth_store.save_truth(axf_id, metric)
        updated.add(axf_id)

    return truth_store.MergeResult(updated_metrics=updated, skipped_metrics=skipped)


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    st.title(APP_TITLE)

    _ensure_dirs()

    # global ingest state
    ingest_state = file_ingest_state.load_state()

    # Sidebar styling (wider + compact file subtext + colored mapping count buttons)
    #
    # NOTE: `components.html(...)` renders in an iframe, so CSS won't affect the parent DOM unless we
    # explicitly inject it into the parent document. We do that here to avoid rendering visible CSS text
    # in the main pane and to keep styling stable across Streamlit versions.
    _SIDEBAR_CSS = r"""
/* Make sidebar thinner (more IDE-like). */
[data-testid="stSidebar"] { width: 340px; min-width: 340px; }
[data-testid="stSidebar"] > div { width: 340px; min-width: 340px; }

.axfTreeHeader { font-size: 0.95rem; font-weight: 650; line-height: 1.1; }
.axfRowName { font-size: 0.95rem; font-weight: 550; line-height: 1.2; }
.axfProgress { font-size: 0.90rem; font-weight: 650; white-space: nowrap; text-align: center; line-height: 1.2; }
.axfProgress.ok { color: #2bd46f; }
.axfProgress.warn { color: #f2c94c; }
.axfProgress.muted { color: #9aa0a6; }

/* --- Dialogs: make mapping popup wider (easier scanning) --- */
div[role="dialog"] {
  max-width: 92vw !important;
}
div[role="dialog"] > div {
  width: min(1100px, 92vw) !important;
  max-width: 92vw !important;
}

/* --- Generate Prompt popover: make it wider --- */
div[data-testid="stPopoverBody"]:has(.axfPromptPopoverMarker) {
  min-width: 640px !important;   /* ~2x wider than default */
  max-width: 900px !important;
}

/* Tighten row spacing + center align within rows */
[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] {
  align-items: center !important;
}
[data-testid="stSidebar"] [data-testid="stColumn"] {
  padding-top: 0 !important;
  padding-bottom: 0 !important;
}
[data-testid="stSidebar"] .element-container {
  margin-bottom: 0.08rem !important;
}
/* Remove default markdown paragraph margins inside sidebar rows */
[data-testid="stSidebar"] .stMarkdown p {
  margin: 0 !important;
  line-height: 1.2 !important;
}

/* --- Sidebar: make capture-type name clickable like text --- */
[data-testid="stSidebar"] div[class*="t-key-open_ct_doc__"] {
  text-align: left !important;
}
[data-testid="stSidebar"] div[class*="t-key-open_ct_doc__"] button {
  background: transparent !important;
  border: none !important;
  box-shadow: none !important;
  padding: 0 !important;
  min-height: unset !important;
  height: auto !important;
  width: 100% !important;
  display: flex !important;
  justify-content: flex-start !important;
  color: inherit !important;
  text-align: left !important;
}
[data-testid="stSidebar"] div[class*="t-key-open_ct_doc__"] button > div {
  /* Streamlit's base button label wrapper is a flex container that defaults to centered content.
     Use a stable selector here (not `st-emotion-cache-*`) to left/top align capture-type names. */
  display: flex !important;
  align-items: flex-start !important;
  justify-content: flex-start !important;
  text-align: left !important;
  width: 100% !important;
}
[data-testid="stSidebar"] div[class*="t-key-open_ct_doc__"] button:hover {
  text-decoration: underline !important;
}
[data-testid="stSidebar"] div[class*="t-key-open_ct_doc__"] button p {
  margin: 0 !important;
  line-height: 1.2 !important;
  font-size: 0.95rem !important;
  font-weight: 550 !important;
}

/* --- Upload button: make Streamlit file uploader look like a tiny icon --- */
[data-testid="stSidebar"] section[data-testid="stFileUploaderDropzone"] {
  padding: 0 !important;
  border: none !important;
  background: transparent !important;
  min-height: unset !important;
}
/* Hide the dropzone copy entirely */
[data-testid="stSidebar"] section[data-testid="stFileUploaderDropzone"] > div,
[data-testid="stSidebar"] section[data-testid="stFileUploaderDropzone"] small,
[data-testid="stSidebar"] section[data-testid="stFileUploaderDropzone"] p {
  display: none !important;
}
/* Icon-only button */
[data-testid="stSidebar"] section[data-testid="stFileUploaderDropzone"] button {
  width: 34px !important;
  height: 30px !important;
  padding: 0 !important;
  border-radius: 8px !important;
  font-size: 0 !important; /* hide label */
  border: 1px solid rgba(224, 224, 224, 35) !important;
  background: #1A1A1A !important;
  color: #E0E0E0 !important;
}
[data-testid="stSidebar"] section[data-testid="stFileUploaderDropzone"] button::before {
  content: "⤒";
  font-size: 16px;
  line-height: 30px;
}
[data-testid="stSidebar"] section[data-testid="stFileUploaderDropzone"] button:hover {
  border: 1px solid rgba(224, 224, 224, 70) !important;
}

/* Small "edit" icon button (no chunky chrome) */
div[class*="t-key-openmap_doc_"] button,
div[class*="t-key-openmap_tex_"] button {
  background: transparent !important;
  border: none !important;
  padding: 0 !important;
  min-height: unset !important;
  height: auto !important;
  box-shadow: none !important;
  color: #9aa0a6 !important;
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
}
div[class*="t-key-openmap_doc_"] button:hover,
div[class*="t-key-openmap_tex_"] button:hover {
  color: #e0e0e0 !important;
}

/* Trash icon: no chunky chrome */
div[class*="t-key-del_doc_"] button,
div[class*="t-key-del_tex_"] button {
  background: transparent !important;
  border: none !important;
  padding: 0 !important;
  min-height: unset !important;
  height: auto !important;
  box-shadow: none !important;
  color: #9aa0a6 !important;
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
}
div[class*="t-key-del_doc_"] button:hover,
div[class*="t-key-del_tex_"] button:hover {
  color: #e0e0e0 !important;
}

/* --- Capture type editor: make reorder buttons compact --- */
div[class*="t-key-ct_up__"] button,
div[class*="t-key-ct_dn__"] button,
div[class*="t-key-ct_del__"] button {
  padding: 0 !important;
  min-height: unset !important;
  height: 22px !important;
  width: 24px !important;
  min-width: 24px !important;
  border-radius: 8px !important;
  display: grid !important;
  place-items: center !important;
  font-size: 12px !important;
  line-height: 1 !important;
  text-align: center !important;
}
/* Card row reorder/delete buttons (main pane) */
div[class*="t-key-ct_card_up__"] button,
div[class*="t-key-ct_card_dn__"] button,
div[class*="t-key-ct_card_del__"] button {
  padding: 0 !important;
  min-height: unset !important;
  height: 22px !important;
  width: 24px !important;
  min-width: 24px !important;
  border-radius: 8px !important;
  display: grid !important;
  place-items: center !important;
  font-size: 12px !important;
  line-height: 1 !important;
  text-align: center !important;
}
div[class*="t-key-ct_up__"] button p,
div[class*="t-key-ct_dn__"] button p,
div[class*="t-key-ct_del__"] button p {
  margin: 0 !important;
  line-height: 1 !important;
}
div[class*="t-key-ct_up__"] button span,
div[class*="t-key-ct_dn__"] button span,
div[class*="t-key-ct_del__"] button span,
div[class*="t-key-ct_up__"] button div,
div[class*="t-key-ct_dn__"] button div,
div[class*="t-key-ct_del__"] button div {
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
}

/* Capture-type editor row + reorder animation */
.ctRow {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0.10rem 0.2rem;
  border-radius: 10px;
}
.ctIdx {
  opacity: 0.65;
  min-width: 2.2rem;
}
.ctLabel {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ctName {
  font-weight: 650;
}
.ctMeta {
  opacity: 0.62;
}
.ctMoved {
  animation: ctMoveFlash 1s ease-out;
}
@keyframes ctMoveFlash {
  0% { transform: translateY(-3px); background: rgba(45, 212, 111, 0.20); }
  25% { transform: translateY(0); background: rgba(45, 212, 111, 0.12); }
  100% { transform: translateY(0); background: transparent; }
}

/* (Popover styling removed; create-popovers now use zero-width unique labels.) */
"""

    components.html(
        f"""
<script>
(() => {{
  try {{
    const css = {json.dumps(_SIDEBAR_CSS)};
    const doc = window.parent && window.parent.document ? window.parent.document : document;
    let el = doc.getElementById("axf_global_sidebar_css");
    if (!el) {{
      el = doc.createElement("style");
      el.id = "axf_global_sidebar_css";
      doc.head.appendChild(el);
    }}
    el.textContent = css;
  }} catch (e) {{
    // no-op; styling is non-critical
  }}
}})();
</script>
        """,
        height=0,
        width=0,
    )

    with st.sidebar:
        st.header("Sources")
        pull_source = st.radio(
            "Pull source",
            options=["prod", "dev"],
            index=0,
            horizontal=True,
            key="pull_source",
        )
        pull_type = st.selectbox("Pull type", options=["CAS", "Capture", "Metric"], key="pull_type")
        if st.button(f"Pull from {pull_source.upper()}", type="primary", use_container_width=True):
            func_name = f"pull_{pull_type.lower()}_from_{pull_source}"
            func = getattr(refresh_sources, func_name, None)
            if func:
                with st.spinner(f"Pulling {pull_type.lower()} from {pull_source} Firebase..."):
                    status = func()
                (st.success if status.ok else st.warning)(status.message)
            else:
                st.error(f"Unknown pull function: {func_name}")

        docs_dir = paths.uploads_docs_dir()
        latex_dir = paths.uploads_latex_dir()
        docs_dir.mkdir(parents=True, exist_ok=True)
        latex_dir.mkdir(parents=True, exist_ok=True)

        if "doc_uploader_version" not in st.session_state:
            st.session_state.doc_uploader_version = 0
        if "tex_uploader_version" not in st.session_state:
            st.session_state.tex_uploader_version = 0

        doc_paths = _list_files(docs_dir, (".docx",))
        tex_paths = _list_files(latex_dir, (".tex",))
        if not doc_paths and not tex_paths:
            st.caption("No files.")

        st.divider()

        d1, d2 = st.columns([0.85, 0.15], vertical_alignment="center")
        with d1:
            st.markdown("<div class='axfTreeHeader'>Docs</div>", unsafe_allow_html=True)
        with d2:
            docs_upload = st.file_uploader(
                "DOCX",
                type=["docx"],
                accept_multiple_files=True,
                label_visibility="collapsed",
                key=f"doc_uploader_{st.session_state.doc_uploader_version}",
            )
        if docs_upload:
            for up in docs_upload:
                saved = _save_uploaded_file(up, docs_dir)
                # auto-ingest immediately
                # metric_index/capture_types are built below; set a flag for post-build ingest
                st.session_state._pending_docx_ingest = st.session_state.get("_pending_docx_ingest", []) + [saved.name]
            st.session_state.doc_uploader_version += 1
            st.rerun()

        if not doc_paths:
            st.caption("No DOCX files.")

        # Sidebar progress should reflect: mapped AND complete (required fields present).
        metric_complete_cache: dict[str, bool] = {}

        def _metric_is_complete(ax: str) -> bool:
            if not isinstance(ax, str) or not ax.strip():
                return False
            ax = ax.strip()
            if ax in metric_complete_cache:
                return metric_complete_cache[ax]
            try:
                mm = truth_store.load_truth_or_base(ax)
                ok = isinstance(mm, dict) and (not llm_prompt.is_incomplete(mm))
            except Exception:
                ok = False
            metric_complete_cache[ax] = ok
            return ok

        for p in doc_paths:
            rec = file_ingest_state.get_file_record(ingest_state, "docx", p.name) or {"total_items": 0, "unresolved": []}
            mapped, total = file_ingest_state.mapped_count(rec)
            resolved_items = rec.get("resolved_items", [])
            if not isinstance(resolved_items, list):
                resolved_items = []
            complete = 0
            for it in resolved_items:
                if not isinstance(it, dict):
                    continue
                ax = it.get("suggested_axf_id")
                if isinstance(ax, str) and _metric_is_complete(ax):
                    complete += 1
            is_complete = total > 0 and complete == total
            cap_id = rec.get("capture_type_id") if isinstance(rec.get("capture_type_id"), str) else ""
            display_name = cap_id or p.stem
            with st.container(border=False):
                c1, c2, c3, c4 = st.columns([0.70, 0.16, 0.07, 0.07], gap="small", vertical_alignment="center")
                with c1:
                    if st.button(display_name, type="tertiary", use_container_width=True, key=f"open_ct_doc__{p.name}"):
                        st.session_state["main_view"] = "Capture type"
                        st.session_state["selected_capture_type"] = display_name
                        st.rerun()
                with c2:
                    label = f"{complete}/{total}" if total else "set"
                    if total == 0:
                        cls = "muted"
                    elif is_complete:
                        cls = "ok"
                    elif mapped == total:
                        cls = "warn"  # fully mapped, but some metrics incomplete
                    else:
                        cls = "warn" if mapped > 0 else "muted"
                    title = f"{complete} complete / {mapped} mapped / {total} total"
                    st.markdown(f"<div class='axfProgress {cls}' title='{title}'>{label}</div>", unsafe_allow_html=True)
                with c3:
                    if st.button("🔗", key=f"openmap_doc_{p.name}", type="tertiary", use_container_width=True):
                        _open_mapping_dialog("docx", p.name)
                with c4:
                    if st.button("🗑", key=f"del_doc_{p.name}", type="tertiary", use_container_width=True):
                        ok, msg = _delete_file_safely(p, docs_dir)
                        (st.success if ok else st.error)(msg)
                        file_ingest_state.delete_file_record(ingest_state, "docx", p.name)
                        file_ingest_state.save_state(ingest_state)

                        # If this file's mapper is open, close it before rerun.
                        info = st.session_state.get("mapping_dialog")
                        if isinstance(info, dict) and info.get("kind") == "docx" and info.get("filename") == p.name:
                            st.session_state.pop("mapping_dialog", None)

                        # Remove from any pending ingest queue.
                        pending = st.session_state.get("_pending_docx_ingest")
                        if isinstance(pending, list):
                            st.session_state["_pending_docx_ingest"] = [x for x in pending if x != p.name]

                        st.rerun()
            # no extra spacing between rows

        st.divider()

        t1, t2 = st.columns([0.85, 0.15], vertical_alignment="center")
        with t1:
            st.markdown("<div class='axfTreeHeader'>LaTeX</div>", unsafe_allow_html=True)
        with t2:
            tex_upload = st.file_uploader(
                "TeX",
                type=["tex"],
                accept_multiple_files=True,
                label_visibility="collapsed",
                key=f"tex_uploader_{st.session_state.tex_uploader_version}",
            )
        if tex_upload:
            for up in tex_upload:
                saved = _save_uploaded_file(up, latex_dir)
                st.session_state._pending_tex_ingest = st.session_state.get("_pending_tex_ingest", []) + [saved.name]
            st.session_state.tex_uploader_version += 1
            st.rerun()

        if not tex_paths:
            st.caption("No LaTeX files.")
        for p in tex_paths:
            rec = file_ingest_state.get_file_record(ingest_state, "latex", p.name) or {"total_items": 0, "unresolved": []}
            mapped, total = file_ingest_state.mapped_count(rec)
            resolved_items = rec.get("resolved_items", [])
            if not isinstance(resolved_items, list):
                resolved_items = []
            complete = 0
            for it in resolved_items:
                if not isinstance(it, dict):
                    continue
                ax = it.get("suggested_axf_id")
                if isinstance(ax, str) and _metric_is_complete(ax):
                    complete += 1
            is_complete = total > 0 and complete == total
            with st.container(border=False):
                c1, c2, c3, c4 = st.columns([0.70, 0.16, 0.07, 0.07], gap="small", vertical_alignment="center")
                with c1:
                    st.markdown(f"<div class='axfRowName'>{p.stem}</div>", unsafe_allow_html=True)
                with c2:
                    label = f"{complete}/{total}" if total else "set"
                    if total == 0:
                        cls = "muted"
                    elif is_complete:
                        cls = "ok"
                    elif mapped == total:
                        cls = "warn"
                    else:
                        cls = "warn" if mapped > 0 else "muted"
                    title = f"{complete} complete / {mapped} mapped / {total} total"
                    st.markdown(f"<div class='axfProgress {cls}' title='{title}'>{label}</div>", unsafe_allow_html=True)
                with c3:
                    if st.button("🔗", key=f"openmap_tex_{p.name}", type="tertiary", use_container_width=True):
                        _open_mapping_dialog("latex", p.name)
                with c4:
                    if st.button("🗑", key=f"del_tex_{p.name}", type="tertiary", use_container_width=True):
                        ok, msg = _delete_file_safely(p, latex_dir)
                        (st.success if ok else st.error)(msg)
                        file_ingest_state.delete_file_record(ingest_state, "latex", p.name)
                        file_ingest_state.save_state(ingest_state)

                        # If this file's mapper is open, close it before rerun.
                        info = st.session_state.get("mapping_dialog")
                        if isinstance(info, dict) and info.get("kind") == "latex" and info.get("filename") == p.name:
                            st.session_state.pop("mapping_dialog", None)

                        # Remove from any pending ingest queue.
                        pending = st.session_state.get("_pending_tex_ingest")
                        if isinstance(pending, list):
                            st.session_state["_pending_tex_ingest"] = [x for x in pending if x != p.name]

                        st.rerun()
            # no extra spacing between rows

        st.divider()

        # On boot: only check local snapshot folders (no automatic DB calls)
        analytics_snapshot_files = list(paths.analytics_db_dir().glob("*.json"))
        capture_snapshot_files = list(paths.capture_config_db_dir().glob("*.json"))
        if not analytics_snapshot_files:
            st.warning("No analytics snapshot files found in `file_system/analytics_db/`. Use 'Pull from PROD' with 'Metric'.")
        if not capture_snapshot_files:
            st.warning("No capture config snapshot files found in `file_system/capture_config_from_db/`. Use 'Pull from PROD' with 'Capture'.")

        capture_types = _list_capture_types_from_snapshots()
        st.caption(f"Capture types found: {len(capture_types)}")

        st.divider()

        st.header("Push options")

        cas_baseline = st.radio(
            "CAS baseline",
            options=["auto", "prod", "dev"],
            index=0,
            horizontal=True,
            key="cas_baseline_choice",
        )
        if st.button("Push CAS Changes (Dev)", use_container_width=True, key="cas_prepare_push"):
            st.session_state.pop("_capcfg_pending", None)
            st.session_state.pop("_metric_pending", None)
            with st.spinner(f"Computing pending CAS changes vs {cas_baseline} baseline..."):
                summary, rows, logs_or_err, to_push, meta = capture_analytic_settings_pipeline.compute_pending_cas_changes(baseline_choice=cas_baseline)
            if summary is None:
                st.session_state.pop("_cas_pending", None)
                st.error(str(logs_or_err or "Failed computing CAS changes."))
            else:
                st.session_state["_cas_pending"] = {
                    "summary": summary,
                    "rows": rows,
                    "logs": logs_or_err,
                    "to_push": to_push,
                    "meta": meta,
                }

        capcfg_baseline = st.radio(
            "Capture baseline",
            options=["auto", "prod", "dev"],
            index=0,
            horizontal=True,
            key="capcfg_baseline_choice",
        )
        if st.button("Push Capture Changes (Dev)", use_container_width=True, key="capcfg_prepare_push"):
            st.session_state.pop("_cas_pending", None)
            st.session_state.pop("_metric_pending", None)
            with st.spinner(f"Computing pending capture-config changes vs {capcfg_baseline} baseline..."):
                summary, rows, logs_or_err, to_push, meta = capture_config_push_pipeline.compute_pending_capture_config_changes(baseline_choice=capcfg_baseline)
            if summary is None:
                st.session_state.pop("_capcfg_pending", None)
                st.error(str(logs_or_err or "Failed computing capture-config changes."))
            else:
                st.session_state["_capcfg_pending"] = {
                    "summary": summary,
                    "rows": rows,
                    "logs": logs_or_err,
                    "to_push": to_push,
                    "meta": meta,
                }

        metric_baseline = st.radio(
            "Metric baseline",
            options=["auto", "prod", "dev"],
            index=0,
            horizontal=True,
            key="metric_baseline_choice",
        )
        if st.button("Push Metric Changes (Dev)", use_container_width=True, key="metric_prepare_push"):
            st.session_state.pop("_cas_pending", None)
            st.session_state.pop("_capcfg_pending", None)
            with st.spinner(f"Computing pending metric changes vs {metric_baseline} baseline..."):
                summary, rows, logs_or_err, to_push, meta = metric_push_pipeline.compute_pending_metric_changes(baseline_choice=metric_baseline)
            if summary is None:
                st.session_state.pop("_metric_pending", None)
                st.error(str(logs_or_err or "Failed computing metric changes."))
            else:
                st.session_state["_metric_pending"] = {
                    "summary": summary,
                    "rows": rows,
                    "logs": logs_or_err,
                    "to_push": to_push,
                    "meta": meta,
                }

    pending = st.session_state.get("_cas_pending")
    if isinstance(pending, dict) and pending.get("summary") is not None:
        summary = pending["summary"]
        rows = pending.get("rows") or []
        logs = pending.get("logs")
        to_push = pending.get("to_push") or []
        meta = pending.get("meta") or {}
        new_ids = meta.get("new") if isinstance(meta, dict) else None
        deleted_ids = meta.get("deleted") if isinstance(meta, dict) else None
        modified_ids = meta.get("modified") if isinstance(meta, dict) else None

        st.divider()
        st.subheader("Pending push (dev)")
        st.warning(
            f"Pending CAS push to dev: {summary.new_count} new, {summary.changed_count} changed, "
            f"{summary.unchanged_count} unchanged (desired={summary.total_desired}, prod_existing={summary.prod_total_existing})."
        )
        if isinstance(new_ids, list) and isinstance(deleted_ids, list) and isinstance(modified_ids, list):
            st.code(
                "\n".join(
                    [
                        f"+ new ({len(new_ids)}): " + (", ".join(new_ids[:10]) + (" …" if len(new_ids) > 10 else "")),
                        f"* modified ({len(modified_ids)}): "
                        + (", ".join(modified_ids[:10]) + (" …" if len(modified_ids) > 10 else "")),
                        f"- deleted_in_local ({len(deleted_ids)}): "
                        + (", ".join(deleted_ids[:10]) + (" …" if len(deleted_ids) > 10 else "")),
                    ]
                )
            )

        st.caption(
            f"Diff baseline: {summary.baseline_source} (last_update_time={summary.baseline_last_update_time}). "
            "Then we build desired CAS from `metrics_truth` and compare. Push target is dev."
        )

        # Table is intentionally omitted here; the per-item diff view below is the primary review UX.

        # Only offer field-by-field diffs for modified entries (exclude new/deleted).
        if isinstance(modified_ids, list) and modified_ids:
            # Auto-populate with the first changed entry for faster review.
            st.session_state.setdefault("cas_diff_pick", modified_ids[0])
            sel = st.selectbox("Inspect a modified item", options=list(modified_ids), index=0, key="cas_diff_pick")
            if isinstance(sel, str) and sel:
                d = capture_analytic_settings_pipeline.describe_cas_diff(sel) or {}
                note = d.get("note")
                if isinstance(note, str) and note:
                    st.caption(note)

                changed = d.get("changed_fields") or []
                before = d.get("before") or {}
                after = d.get("after") or {}

                with st.expander("Differences (prod → local desired)", expanded=True):
                    for field in changed:
                        if not isinstance(field, str) or not field:
                            continue
                        b = before.get(field)
                        a = after.get(field)
                        st.markdown(f"**{field}**")
                        lcol, rcol = st.columns(2, gap="large")
                        with lcol:
                            st.caption("Before (prod)")
                            st.json(b)
                        with rcol:
                            st.caption("After (local)")
                            st.json(a)

        c_ok, c_cancel = st.columns([0.70, 0.30], vertical_alignment="center")
        with c_ok:
            if st.button("Confirm push to axioforce-dev", type="primary", use_container_width=True, key="cas_confirm_push"):
                with st.spinner("Pushing CAS documents to dev..."):
                    ok, out = capture_analytic_settings_pipeline.push_cas_to_dev()
                if ok:
                    st.success("CAS push complete (dev).")
                    st.session_state.pop("_cas_pending", None)
                    st.rerun()
                else:
                    st.error(f"CAS push failed.\n\n{out}")
        with c_cancel:
            if st.button("Cancel", use_container_width=True, key="cas_cancel_push"):
                st.session_state.pop("_cas_pending", None)
                st.rerun()

        if isinstance(logs, str) and logs.strip():
            with st.expander("Logs", expanded=False):
                st.text(logs)

        # Keep CAS review front-and-center; don't render the rest of the editor UI.
        return

    pending2 = st.session_state.get("_capcfg_pending")
    if isinstance(pending2, dict) and pending2.get("summary") is not None:
        summary = pending2["summary"]
        rows = pending2.get("rows") or []
        logs = pending2.get("logs")
        to_push = pending2.get("to_push") or []
        meta = pending2.get("meta") or {}
        new_ids = meta.get("new") if isinstance(meta, dict) else None
        deleted_ids = meta.get("deleted") if isinstance(meta, dict) else None
        modified_ids = meta.get("modified") if isinstance(meta, dict) else None

        st.divider()
        st.subheader("Pending push (dev)")
        st.warning(
            f"Pending capture-config push to dev: {summary.new_count} new, {summary.changed_count} changed, "
            f"{summary.unchanged_count} unchanged (local={summary.total_local}, prod_existing={summary.prod_total_existing})."
        )
        if isinstance(new_ids, list) and isinstance(deleted_ids, list) and isinstance(modified_ids, list):
            st.code(
                "\n".join(
                    [
                        f"+ new ({len(new_ids)}): " + (", ".join(new_ids[:10]) + (" …" if len(new_ids) > 10 else "")),
                        f"* modified ({len(modified_ids)}): "
                        + (", ".join(modified_ids[:10]) + (" …" if len(modified_ids) > 10 else "")),
                        f"- deleted_in_local ({len(deleted_ids)}): "
                        + (", ".join(deleted_ids[:10]) + (" …" if len(deleted_ids) > 10 else "")),
                    ]
                )
            )
        st.caption(
            f"Diff baseline: {summary.baseline_source} (last_update_time={summary.baseline_last_update_time}). "
            "Then we compare against local `capture_config_from_db`. Push target is dev."
        )

        # Allow selecting specific captures to push
        if "capcfg_selected_to_push" not in st.session_state:
            st.session_state["capcfg_selected_to_push"] = to_push
        selected_to_push = st.multiselect(
            "Select captures to push",
            options=to_push,
            key="capcfg_selected_to_push"
        )

        # Table is intentionally omitted here; use the per-item diff view below.

        # Only offer diffs for modified entries (exclude new/deleted).
        if isinstance(modified_ids, list) and modified_ids:
            st.session_state.setdefault("capcfg_diff_pick", modified_ids[0])
            sel = st.selectbox("Inspect a modified item", options=list(modified_ids), index=0, key="capcfg_diff_pick")
            if isinstance(sel, str) and sel:
                d = capture_config_push_pipeline.describe_capture_config_diff(sel) or {}
                note = d.get("note")
                if isinstance(note, str) and note:
                    st.caption(note)

                other_changed = d.get("other_changed_fields") or []
                before_other = d.get("before_other") or {}
                after_other = d.get("after_other") or {}
                metric_changes = d.get("metric_changes") or {}

                with st.expander("Differences (prod → local)", expanded=True):
                    # Metric summary INSIDE the card (what you asked for)
                    added = metric_changes.get("added") or []
                    removed = metric_changes.get("removed") or []
                    modified_m = metric_changes.get("modified") or []
                    unaffected = metric_changes.get("unaffected") or []
                    unaffected_count = int(metric_changes.get("unaffected_count") or 0)
                    st.code(
                        "\n".join(
                            [
                                f"+ metrics_added ({len(added)}): "
                                + (", ".join(added[:12]) + (" …" if len(added) > 12 else "")),
                                f"* metrics_modified ({len(modified_m)}): "
                                + (", ".join(modified_m[:12]) + (" …" if len(modified_m) > 12 else "")),
                                f"- metrics_removed ({len(removed)}): "
                                + (", ".join(removed[:12]) + (" …" if len(removed) > 12 else "")),
                                f"= metrics_unaffected ({unaffected_count}): "
                                + (
                                    ", ".join([str(x) for x in unaffected[:12] if str(x).strip()])
                                    + (" …" if isinstance(unaffected, list) and len(unaffected) > 12 else "")
                                ),
                            ]
                        )
                    )

                    # For per-metric diffs, ignore purely added/removed noise by focusing on modified signatures only.
                    metric_diffs = metric_changes.get("metric_diffs") if isinstance(metric_changes, dict) else None
                    if isinstance(metric_diffs, dict) and metric_diffs:
                        st.markdown("**Metric changes (existing metrics)**")
                        for mid in (metric_changes.get("modified") or []):
                            if mid not in metric_diffs:
                                continue
                            entry = metric_diffs.get(mid) or {}
                            before_sig = entry.get("before") or {}
                            after_sig = entry.get("after") or {}

                            before_pri = before_sig.get("priority_indices") or []
                            after_pri = after_sig.get("priority_indices") or []
                            before_w = before_sig.get("wiring") or {}
                            after_w = after_sig.get("wiring") or {}

                            pri_changed = before_pri != after_pri or (before_sig.get("in_priority") != after_sig.get("in_priority"))
                            wiring_changed = before_w != after_w

                            st.markdown(f"**{mid}**")

                            if pri_changed:
                                st.caption(f"Priority index: {before_pri} → {after_pri}")

                            if wiring_changed:
                                st.markdown("Wiring changes")
                                lcol, rcol = st.columns(2, gap="large")
                                with lcol:
                                    st.caption("Before (prod)")
                                    st.json(before_w)
                                with rcol:
                                    st.caption("After (local)")
                                    st.json(after_w)

                    if other_changed:
                        st.markdown("**Other config changes (non-metric fields)**")
                        for field in other_changed:
                            if not isinstance(field, str) or not field:
                                continue
                            b = before_other.get(field)
                            a = after_other.get(field)
                            st.markdown(f"**{field}**")
                            lcol, rcol = st.columns(2, gap="large")
                            with lcol:
                                st.caption("Before (prod)")
                                st.json(b)
                            with rcol:
                                st.caption("After (local)")
                                st.json(a)

        c_ok, c_cancel = st.columns([0.70, 0.30], vertical_alignment="center")
        with c_ok:
            if st.button(
                f"Confirm push {len(selected_to_push)} capture configs to axioforce-dev",
                type="primary",
                use_container_width=True,
                key="capcfg_confirm_push",
                disabled=(len(selected_to_push) == 0),
            ):
                with st.spinner("Pushing capture configs to dev..."):
                    ok, out = capture_config_push_pipeline.push_capture_configs_to_dev(list(selected_to_push))
                if ok:
                    st.success("Capture-config push complete (dev).")
                    st.session_state.pop("_capcfg_pending", None)
                    st.rerun()
                else:
                    st.error(f"Capture-config push failed.\n\n{out}")
        with c_cancel:
            if st.button("Cancel", use_container_width=True, key="capcfg_cancel_push"):
                st.session_state.pop("_capcfg_pending", None)
                st.rerun()

        if isinstance(logs, str) and logs.strip():
            with st.expander("Logs", expanded=False):
                st.text(logs)

        return

    pending3 = st.session_state.get("_metric_pending")
    if isinstance(pending3, dict) and pending3.get("summary") is not None:
        def _render_json_value(v):
            """
            Streamlit's st.json treats *strings* as raw JSON text and tries to parse them.
            Many metric fields are plain strings (units like '% BW', latex with backslashes),
            so we wrap scalars via json.dumps before rendering.
            """
            import json as _json

            if isinstance(v, (dict, list)):
                st.json(v)
                return
            try:
                st.json(_json.dumps(v, ensure_ascii=False))
            except Exception:
                # Last resort: show a repr as code
                st.code(repr(v))

        summary = pending3["summary"]
        logs = pending3.get("logs")
        meta = pending3.get("meta") or {}
        new_ids = meta.get("new") if isinstance(meta, dict) else None
        modified_ids = meta.get("modified") if isinstance(meta, dict) else None

        st.divider()
        st.subheader("Pending push (dev)")
        st.warning(
            f"Pending metric push to dev: {summary.new_count} new, {summary.changed_count} changed, "
            f"{summary.unchanged_count} unchanged (local={summary.total_local}, baseline_existing={summary.baseline_total_existing})."
        )
        if isinstance(new_ids, list) and isinstance(modified_ids, list):
            st.code(
                "\n".join(
                    [
                        f"+ new ({len(new_ids)}): " + (", ".join(new_ids[:12]) + (" …" if len(new_ids) > 12 else "")),
                        f"* modified ({len(modified_ids)}): "
                        + (", ".join(modified_ids[:12]) + (" …" if len(modified_ids) > 12 else "")),
                    ]
                )
            )

        st.caption(
            f"Diff baseline: {summary.baseline_source} (last_update_time={summary.baseline_last_update_time}). "
            "Compare baseline analytics against local `metrics_truth`. Push target is dev."
        )

        scope = st.radio(
            "Push scope",
            options=["New + changed", "New only"],
            horizontal=True,
            key="metric_push_scope",
        )
        selected_to_push = (
            list(new_ids or []) + list(modified_ids or [])
            if scope == "New + changed"
            else list(new_ids or [])
        )

        # Only offer diffs for modified metrics (new metrics are shown in the summary line).
        if scope == "New + changed" and isinstance(modified_ids, list) and modified_ids:
            st.session_state.setdefault("metric_diff_pick", modified_ids[0])
            sel = st.selectbox("Inspect a modified metric", options=list(modified_ids), index=0, key="metric_diff_pick")
            if isinstance(sel, str) and sel:
                d = metric_push_pipeline.describe_metric_diff(sel) or {}
                note = d.get("note")
                if isinstance(note, str) and note:
                    st.caption(note)
                changed = d.get("changed_fields") or []
                before = d.get("before") or {}
                after = d.get("after") or {}

                with st.expander("Differences (baseline → local)", expanded=True):
                    for field in changed:
                        if not isinstance(field, str) or not field:
                            continue
                        b = before.get(field)
                        a = after.get(field)
                        st.markdown(f"**{field}**")
                        lcol, rcol = st.columns(2, gap="large")
                        with lcol:
                            st.caption("Before (baseline)")
                            _render_json_value(b)
                        with rcol:
                            st.caption("After (local)")
                            _render_json_value(a)

        c_ok, c_cancel = st.columns([0.70, 0.30], vertical_alignment="center")
        with c_ok:
            if st.button(
                f"Confirm push {len(selected_to_push)} metrics to axioforce-dev",
                type="primary",
                use_container_width=True,
                key="metric_confirm_push",
                disabled=(len(selected_to_push) == 0),
            ):
                with st.spinner("Pushing metrics to dev..."):
                    ok, out = metric_push_pipeline.push_metrics_to_dev(list(selected_to_push))
                if ok:
                    st.success("Metric push complete (dev).")
                    st.session_state.pop("_metric_pending", None)
                    st.rerun()
                else:
                    st.error(f"Metric push failed.\n\n{out}")
        with c_cancel:
            if st.button("Cancel", use_container_width=True, key="metric_cancel_push"):
                st.session_state.pop("_metric_pending", None)
                st.rerun()

        if isinstance(logs, str) and logs.strip():
            with st.expander("Logs", expanded=False):
                st.text(logs)

        return

    base_metrics = _list_metrics_from_snapshots()
    metric_index = analytics_index.build_metric_index(base_metrics)

    # Ensure we have ingest records for existing files and auto-ingest newly uploaded files.
    # Also, if a file changed on disk, recompute its record.
    for p in _list_files(paths.uploads_docs_dir(), (".docx",)):
        rec = file_ingest_state.get_file_record(ingest_state, "docx", p.name)
        if rec is None or rec.get("mtime") != p.stat().st_mtime or rec.get("ingest_version") != INGEST_VERSION:
            _docx_ingest_and_update_state(p, metric_index, capture_types, ingest_state)
    for p in _list_files(paths.uploads_latex_dir(), (".tex",)):
        rec = file_ingest_state.get_file_record(ingest_state, "latex", p.name)
        if rec is None or rec.get("mtime") != p.stat().st_mtime or rec.get("ingest_version") != INGEST_VERSION:
            _latex_ingest_and_update_state(p, metric_index, ingest_state)

    # Handle newly uploaded files (auto-ingest only; no auto-open UI)
    pending_docs = st.session_state.pop("_pending_docx_ingest", [])
    for name in pending_docs:
        p = paths.uploads_docs_dir() / name
        if p.exists():
            _docx_ingest_and_update_state(p, metric_index, capture_types, ingest_state)

    pending_tex = st.session_state.pop("_pending_tex_ingest", [])
    for name in pending_tex:
        p = paths.uploads_latex_dir() / name
        if p.exists():
            _latex_ingest_and_update_state(p, metric_index, ingest_state)

    file_ingest_state.save_state(ingest_state)

    # Deprecated global manual mapping UI (handled per-file now)
    manual_map = manual_mapping.load_manual_map()
    base_ids = [m.get("axf_id") for m in base_metrics if isinstance(m.get("axf_id"), str)]
    truth_ids = metric_create.list_truth_metric_ids()
    metric_axf_ids = sorted({*base_ids, *truth_ids})

    if not metric_axf_ids:
        st.error("No metrics found in file_system/analytics_db. Refresh from DB or check the folder.")
        return

    # Metric dropdown: show new/incomplete first, mark with "*"
    base_name_map: dict[str, str] = {
        m.get("axf_id"): m.get("name")
        for m in base_metrics
        if isinstance(m.get("axf_id"), str) and isinstance(m.get("name"), str)
    }

    def _is_incomplete(metric: dict[str, Any]) -> bool:
        # "Incomplete is missing name, description, equation, script, or latex formula"
        def _has(v: Any) -> bool:
            return isinstance(v, str) and v.strip() != ""

        return not (
            _has(metric.get("name"))
            and _has(metric.get("description"))
            and _has(metric.get("equation"))
            and _has(metric.get("script"))
            and _has(metric.get("latex_formula"))
        )

    metric_flags: dict[str, dict[str, Any]] = {}
    for ax in metric_axf_ids:
        if not isinstance(ax, str) or not ax:
            continue
        # If the user is actively editing a metric, prefer the in-progress draft for the
        # incomplete flag and display name (so "*" updates immediately after edits).
        draft_key = f"_metric_draft__{ax}"
        draft = st.session_state.get(draft_key)

        base_exists = truth_store.base_metric_path(ax).exists()
        truth_exists = truth_store.truth_metric_path(ax).exists()
        is_new = truth_exists and (not base_exists)

        # Only evaluate "incomplete" for truth metrics (or truth-only). Base snapshot metrics
        # are treated as complete by default to avoid surfacing hundreds of legacy items.
        is_incomplete = False
        name = base_name_map.get(ax, ax)
        if isinstance(draft, dict) and draft:
            try:
                n2 = draft.get("name")
                if isinstance(n2, str) and n2.strip():
                    name = n2.strip()
                is_incomplete = _is_incomplete(draft)
            except Exception:
                is_incomplete = True
        elif truth_exists or (not base_exists):
            try:
                mm = truth_store.load_truth_or_base(ax)
                n2 = mm.get("name")
                if isinstance(n2, str) and n2.strip():
                    name = n2.strip()
                is_incomplete = _is_incomplete(mm)
            except Exception:
                is_incomplete = True

        metric_flags[ax] = {"name": name, "new": is_new, "incomplete": is_incomplete}

    def _metric_sort_key(ax: str) -> tuple[int, int, str]:
        f = metric_flags.get(ax) or {}
        is_new = bool(f.get("new"))
        is_incomplete = bool(f.get("incomplete"))
        prioritized = is_new or is_incomplete
        nm = str(f.get("name") or ax).lower()
        # prioritized first; then new before incomplete; then alpha
        return (0 if prioritized else 1, 0 if is_new else (1 if is_incomplete else 2), nm)

    metric_axf_ids_ordered = sorted([ax for ax in metric_axf_ids if isinstance(ax, str)], key=_metric_sort_key)

    def _fmt_metric(ax: str) -> str:
        f = metric_flags.get(ax) or {}
        nm = str(f.get("name") or ax)
        is_new = bool(f.get("new"))
        is_incomplete = bool(f.get("incomplete"))
        # Only mark with "*" when incomplete. (New-but-complete metrics should not look broken.)
        star = "* " if is_incomplete else ""
        return f"{star}{nm}  ({ax})"


    # Main editor (single column; metric selector lives in the header area)
    center = st.container()

    def _render_mapping_panel() -> None:
        info = st.session_state.get("mapping_dialog") or {}
        kind = info.get("kind")
        filename = info.get("filename")
        if kind not in ("docx", "latex") or not isinstance(filename, str):
            st.write("No mapping context.")
            return

        rec = file_ingest_state.get_file_record(ingest_state, kind, filename) or {}
        unresolved = rec.get("unresolved", [])
        if not isinstance(unresolved, list):
            unresolved = []

        # DOCX: choose capture type once, up-front.
        capture_type_id = ""
        if kind == "docx":
            cap_existing = rec.get("capture_type_id") if isinstance(rec.get("capture_type_id"), str) else ""
            if not cap_existing:
                st.caption(filename)

                suggested = (
                    rec.get("suggested_capture_type_id")
                    if isinstance(rec.get("suggested_capture_type_id"), str)
                    else None
                )

                cap_options = [""] + capture_types
                default_idx = (cap_options.index(suggested) if suggested in cap_options else 0)
                cap = st.selectbox("Capture type", options=cap_options, index=default_idx)

                b1, b2, b3 = st.columns([1, 3, 1], vertical_alignment="center")
                with b2:
                    if st.button("Confirm capture type", type="primary", disabled=(not cap), use_container_width=True):
                        rec["capture_type_id"] = cap
                        p = paths.uploads_docs_dir() / filename
                        if p.exists():
                            new_path = _safe_rename_into_dir(p, cap, paths.uploads_docs_dir())
                            if new_path.name != filename:
                                file_ingest_state.delete_file_record(ingest_state, "docx", filename)
                                filename = new_path.name
                                info["filename"] = filename
                                st.session_state.mapping_dialog = info

                        file_ingest_state.upsert_file_record(ingest_state, "docx", filename, rec)
                        file_ingest_state.save_state(ingest_state)

                        # Re-ingest once (applies capture type and populates unresolved list).
                        p2 = paths.uploads_docs_dir() / filename
                        if p2.exists():
                            _docx_ingest_and_update_state(p2, metric_index, capture_types, ingest_state)
                            file_ingest_state.save_state(ingest_state)

                        st.rerun()
                return
            capture_type_id = cap_existing

        def _set_metric_priority_from_docx(*, manual_map_override: dict[str, Any]) -> tuple[bool, str]:
            """
            Overwrite this capture type's `metric_priority` list to match the
            mapped metrics in this DOCX (in document order).
            """
            if kind != "docx":
                return False, "Only DOCX files have a capture type."
            cap = (capture_type_id or "").strip()
            if not cap:
                return False, "Capture type is not set."

            # Need the underlying doc blocks (document order).
            doc_path = paths.uploads_docs_dir() / filename
            if not doc_path.exists():
                return False, f"DOCX not found: {filename}"

            try:
                lines = docx_ingest.extract_docx_lines(str(doc_path))
                blocks = docx_ingest.parse_metric_blocks_from_lines(lines)
            except Exception as e:
                return False, f"Failed to parse DOCX: {e}"

            # Use the *current* mapping selections the user is looking at (not a stale record copy).
            mm = manual_map_override if isinstance(manual_map_override, dict) else {}

            # Resolve each block to an axf_id (manual_map-aware) in document order.
            # We *write* a unique list to metric_priority (duplicates are not useful there),
            # but we report any duplicates so it's obvious why 12 doc rows can become 11.
            resolved_rows: list[tuple[str, str]] = []  # (doc_name, axf_id)
            for b in blocks:
                axf_id, _warn = analytics_index.resolve_metric_axf_id(b.name, metric_index, manual_map=mm)
                if not isinstance(axf_id, str) or not axf_id.strip():
                    continue
                resolved_rows.append((b.name, axf_id))

            if not resolved_rows:
                return False, "No mapped metrics found in this document."

            unique_axf_ids: list[str] = []
            first_doc_for_id: dict[str, str] = {}
            dup_notes: list[str] = []
            for doc_name, axf_id in resolved_rows:
                if axf_id in first_doc_for_id:
                    dup_notes.append(f"- `{doc_name}` → `{axf_id}` (already used by `{first_doc_for_id[axf_id]}`)")
                    continue
                first_doc_for_id[axf_id] = doc_name
                unique_axf_ids.append(axf_id)

            cfg_path = paths.capture_config_db_dir() / f"{cap}.json"
            if not cfg_path.exists():
                return False, f"Capture config not found: {cap}.json"

            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8", errors="ignore"))
                if not isinstance(cfg, dict):
                    return False, "Capture config JSON root must be an object."
            except Exception as e:
                return False, f"Failed to load capture config: {e}"

            existing_pri = cfg.get("metric_priority", [])
            existing_entries: list[dict[str, Any]] = [x for x in existing_pri if isinstance(x, dict)]
            by_id: dict[str, dict[str, Any]] = {}
            for ent in existing_entries:
                ax0 = ent.get("axf_id")
                if isinstance(ax0, str) and ax0 and ax0 not in by_id:
                    by_id[ax0] = ent

            new_entries: list[dict[str, Any]] = []
            for ax0 in unique_axf_ids:
                base_ent = dict(by_id.get(ax0, {"axf_id": ax0, "axis": None, "phase": None, "device": None}))
                base_ent["axf_id"] = ax0
                new_entries.append(base_ent)

            cfg["metric_priority"] = new_entries
            try:
                cfg_path.write_text(json.dumps(cfg, indent=4, ensure_ascii=False), encoding="utf-8")
            except Exception as e:
                return False, f"Failed to save capture config: {e}"

            metrics_list = ", ".join(unique_axf_ids[:10])
            more_metrics = "" if len(unique_axf_ids) <= 10 else f", ... (+{len(unique_axf_ids) - 10} more)"
            if dup_notes:
                details = "\n".join(dup_notes[:6])
                more = "" if len(dup_notes) <= 6 else f"\n- ... and {len(dup_notes) - 6} more"
                return (
                    True,
                    f"Set key metrics for '{cap}' from '{filename}': {metrics_list}{more_metrics}\n\n"
                    f"({len(new_entries)} unique metrics from {len(resolved_rows)} doc rows.)\n\n"
                    f"Duplicates were collapsed:\n{details}{more}",
                )
            return True, f"Set key metrics for '{cap}' from '{filename}': {metrics_list}{more_metrics} ({len(new_entries)} metrics, doc order)."

        # Build a single list so the user can review/confirm everything at once.
        manual_map = rec.get("manual_map") if isinstance(rec.get("manual_map"), dict) else {}
        resolved_items = rec.get("resolved_items", [])
        if not isinstance(resolved_items, list):
            resolved_items = []
        all_items: list[dict[str, Any]] = []
        for it in resolved_items:
            if isinstance(it, dict):
                all_items.append(it)
        for it in unresolved:
            if isinstance(it, dict):
                all_items.append(it)

        if not all_items:
            st.caption("No metrics to map.")
            return

        st.markdown(f"**Mappings ({len(all_items)})**")
        st.caption(filename)

        # Build name map from BOTH base snapshot metrics and truth-only metrics.
        # (Truth-only metrics are created locally and won't exist in analytics_db snapshots.)
        axf_to_name: dict[str, str] = {
            m.get("axf_id"): m.get("name")
            for m in base_metrics
            if isinstance(m.get("axf_id"), str) and isinstance(m.get("name"), str)
        }
        for ax in metric_axf_ids:
            if not isinstance(ax, str) or not ax or ax in axf_to_name:
                continue
            try:
                mm = truth_store.load_truth_or_base(ax)
                nm = mm.get("name")
                if isinstance(nm, str) and nm.strip():
                    axf_to_name[ax] = nm.strip()
            except Exception:
                # Best-effort only
                pass

        all_axf_ids_sorted = sorted(
            [a for a in metric_axf_ids if isinstance(a, str) and a in axf_to_name],
            key=lambda a: axf_to_name[a].lower(),
        )

        def _priority_axf_ids(ct: str) -> list[str]:
            if not ct:
                return []
            p = paths.capture_config_db_dir() / f"{ct}.json"
            if not p.exists():
                return []
            try:
                cfg = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                return []

            pri = cfg.get("metric_priority")
            out: list[str] = []
            if isinstance(pri, list):
                for it in pri:
                    if isinstance(it, dict):
                        ax = it.get("axf_id")
                        if isinstance(ax, str) and ax and ax not in out:
                            out.append(ax)
            return out

        pri_ids = _priority_axf_ids(capture_type_id) if kind == "docx" else []
        pri_ids = [a for a in pri_ids if a in axf_to_name]
        rest_ids = [a for a in all_axf_ids_sorted if a not in set(pri_ids)]

        # Always show the full metric list (priority still appears first).
        show_all = True

        # Even when we're in "priority-only" mode, always include the metrics we already mapped
        # (or auto-suggested) for the items shown in this dialog. Otherwise they appear blank,
        # which is confusing and makes the dialog look like it has more "unmapped" rows than the
        # sidebar count (which is based solely on `unresolved` length).
        forced_ids: list[str] = []
        try:
            # manual picks
            if isinstance(manual_map, dict):
                for v in manual_map.values():
                    if isinstance(v, str) and v and v in axf_to_name:
                        forced_ids.append(v)
            # auto suggestions (resolved + unresolved)
            for it in all_items:
                ax = it.get("suggested_axf_id")
                if isinstance(ax, str) and ax and ax in axf_to_name:
                    forced_ids.append(ax)
        except Exception:
            forced_ids = []

        extra_ids: list[str] = []
        seen_extra: set[str] = set(pri_ids)
        for ax in forced_ids:
            if ax not in seen_extra:
                extra_ids.append(ax)
                seen_extra.add(ax)

        options = [""] + pri_ids + extra_ids + (rest_ids if (show_all or not pri_ids) else [])

        already_mapped_axf_ids: set[str] = set()
        try:
            # Anything already resolved for this file should be marked, not only manual picks.
            resolved_list = rec.get("resolved_axf_ids", [])
            if isinstance(resolved_list, list):
                already_mapped_axf_ids |= {x for x in resolved_list if isinstance(x, str) and x.strip()}
            if isinstance(manual_map, dict):
                already_mapped_axf_ids |= {v for v in manual_map.values() if isinstance(v, str) and v.strip()}
        except Exception:
            already_mapped_axf_ids = set()

        def _fmt(opt: str) -> str:
            if opt == "":
                return ""
            name = axf_to_name.get(opt, opt)
            mark = "✓ " if opt in already_mapped_axf_ids else ""
            return f"{mark}{name}  ({opt})"

        changed = False
        for it in all_items:
            doc_name = str(it.get("doc_name") or "")
            sig = it.get("sig") if isinstance(it.get("sig"), str) else ""
            reason = str(it.get("reason") or "")
            doc_fields = it.get("doc_fields") if isinstance(it.get("doc_fields"), dict) else {}

            current = manual_map.get(sig) if sig else ""
            suggested_axf = it.get("suggested_axf_id") if isinstance(it.get("suggested_axf_id"), str) else ""
            if not current and suggested_axf in options:
                current = suggested_axf
            if current not in options:
                current = ""

            c1, c2, c3 = st.columns([0.55, 0.37, 0.08], vertical_alignment="center")
            with c1:
                st.markdown(f"**{doc_name}**")
                # Keep the UI clean: don't show resolution-reason labels (manual_map/fuzzy_match/etc.).
            with c2:
                pick_key = f"mm_pick_{kind}_{filename}_{sig}"
                # Streamlit doesn't allow modifying a widget's session_state value after the widget
                # is instantiated in the same run. When we "Create & map", we stash a forced value
                # and apply it here on the next rerun (before the selectbox is created).
                force_key = f"_mm_force_pick__{kind}__{filename}__{sig}"
                forced = st.session_state.pop(force_key, None)
                if isinstance(forced, str) and forced:
                    st.session_state[pick_key] = forced
                # Avoid Streamlit warning: don't pass a default `index` if we're also
                # driving the value via session_state for this widget key.
                if pick_key in st.session_state:
                    picked = st.selectbox(
                        "Map to",
                        options=options,
                        format_func=_fmt,
                        key=pick_key,
                        label_visibility="collapsed",
                    )
                else:
                    picked = st.selectbox(
                        "Map to",
                        options=options,
                        format_func=_fmt,
                        index=options.index(current) if current in options else 0,
                        key=pick_key,
                        label_visibility="collapsed",
                    )
            with c3:
                # Show "Create" whenever the mapping is currently blank.
                # This supports the workflow: map something → change to blank/null → create a new metric.
                can_create = bool(sig) and (picked == "")
                if can_create:
                    # NOTE: older Streamlit versions don't support `key=` for popover.
                    # We keep the UI looking like a plain "＋" by appending an invisible
                    # (zero-width) unique suffix so widget IDs don't collide in the loop.
                    pop_label = "＋" + _zw_unique(f"mm_create_{kind}_{filename}_{sig}")
                    with st.popover(pop_label, help="Create a new metric from this doc row", use_container_width=False):
                        existing_ids = set(metric_axf_ids)
                        suggested_id = metric_create.suggest_axf_id(doc_name, existing_ids)
                        axf_id_in = st.text_input(
                            "New axf_id",
                            value=suggested_id,
                            key=f"mm_new_axf__{kind}__{filename}__{sig}",
                            help="Must be unique. Example: peakLandingForceAsymmetry",
                        ).strip()

                        if st.button(
                            "Create & map",
                            type="primary",
                            disabled=(not axf_id_in),
                            use_container_width=True,
                            key=f"mm_create_go__{kind}__{filename}__{sig}",
                        ):
                            if axf_id_in in existing_ids:
                                st.error(f"`{axf_id_in}` already exists. Pick a different axf_id.")
                            else:
                                draft = metric_create.draft_metric_from_doc_item(
                                    kind=kind,
                                    doc_name=doc_name,
                                    capture_type_id=(capture_type_id if kind == "docx" else None),
                                    doc_fields=doc_fields,
                                )
                                truth_store.save_truth(axf_id_in, draft)

                                manual_map[sig] = axf_id_in
                                rec["manual_map"] = manual_map
                                file_ingest_state.upsert_file_record(ingest_state, kind, filename, rec)
                                file_ingest_state.save_state(ingest_state)

                                # Force the row's selectbox to show the new mapping on the next rerun
                                # (can't modify widget state after instantiation).
                                st.session_state[force_key] = axf_id_in

                                # Re-ingest to update unresolved/resolved lists immediately.
                                if kind == "docx":
                                    p = paths.uploads_docs_dir() / filename
                                    if p.exists():
                                        _docx_ingest_and_update_state(p, metric_index, capture_types, ingest_state)
                                else:
                                    p = paths.uploads_latex_dir() / filename
                                    if p.exists():
                                        _latex_ingest_and_update_state(p, metric_index, ingest_state)
                                file_ingest_state.save_state(ingest_state)

                                st.success(f"Created `{axf_id_in}` and mapped `{doc_name}`.")
                                st.rerun()

            if sig:
                if isinstance(picked, str) and picked.strip():
                    if manual_map.get(sig) != picked:
                        manual_map[sig] = picked
                        changed = True
                else:
                    # Treat clearing the mapping as an explicit "unmap" so the sidebar count
                    # reflects it even if auto-matching would have found a fuzzy suggestion.
                    if (sig not in manual_map) or (manual_map.get(sig) is not None):
                        manual_map[sig] = None
                        changed = True

        # Apply changes immediately (no extra "save/apply" buttons).
        if changed:
            rec["manual_map"] = manual_map
            file_ingest_state.upsert_file_record(ingest_state, kind, filename, rec)
            file_ingest_state.save_state(ingest_state)

            if kind == "docx":
                p = paths.uploads_docs_dir() / filename
                if p.exists():
                    _docx_ingest_and_update_state(p, metric_index, capture_types, ingest_state)
            else:
                p = paths.uploads_latex_dir() / filename
                if p.exists():
                    _latex_ingest_and_update_state(p, metric_index, ingest_state)
            file_ingest_state.save_state(ingest_state)
            st.rerun()

        st.caption("Changes apply immediately.")

        b1, b2, b3 = st.columns([1, 1, 2], vertical_alignment="center")
        with b2:
            # Gate on the latest on-disk ingest record (not just this in-memory `rec` copy),
            # so it's accurate after live updates.
            rec_latest = file_ingest_state.get_file_record(ingest_state, kind, filename) or {}
            unresolved_latest = rec_latest.get("unresolved", [])
            if not isinstance(unresolved_latest, list):
                unresolved_latest = []

            fully_mapped = (not unresolved_latest) and (kind == "docx") and bool(capture_type_id)
            if st.button(
                "Set key metrics from doc",
                disabled=(not fully_mapped),
                help=(
                    "Enabled when all metrics are mapped and capture type is set."
                    if not fully_mapped
                    else "Overwrite this capture type's metric_priority to match the DOCX (doc order)."
                ),
                use_container_width=True,
            ):
                # Persist current manual_map selections first (so DOCX->axf_id resolution is accurate).
                rec["manual_map"] = manual_map
                file_ingest_state.upsert_file_record(ingest_state, kind, filename, rec)
                file_ingest_state.save_state(ingest_state)

                ok, msg = _set_metric_priority_from_docx(manual_map_override=manual_map)
                (st.success if ok else st.error)(msg)
                if ok:
                    st.rerun()

    with center:
        # When the user clicks 🔗 in the sidebar, take over the main view (no popup).
        if st.session_state.get("mapping_dialog"):
            h1, h2 = st.columns([0.14, 0.86], vertical_alignment="center")
            with h1:
                if st.button("← Back", use_container_width=True):
                    st.session_state.pop("mapping_dialog", None)
                    st.rerun()
            with h2:
                st.subheader("Manual mappings")
            _render_mapping_panel()
            return

        # Use a radio instead of tabs so the UI can jump programmatically
        # (e.g. clicking a DOC row name opens the capture type editor).
        view = st.radio(
            "View",
            options=["Capture type", "Metric editor"],
            index=0,
            horizontal=True,
            key="main_view",
            label_visibility="collapsed",
        )

        if view == "Capture type":
            _ = render_capture_type_editor(capture_types=capture_types, base_metrics=base_metrics)
        else:
            top1, top2 = st.columns([0.88, 0.12], vertical_alignment="center")
            with top1:
                # Selection stability: the metric list can re-sort on reruns (e.g. when
                # "incomplete" status changes). If Streamlit can't find the current
                # selection in the new options list, it will fall back to the first option.
                # Preserve the last valid selection instead of jumping unexpectedly.
                _last_sel_key = "_selected_metric_last_valid"
                cur_sel = st.session_state.get("selected_metric")
                last_sel = st.session_state.get(_last_sel_key)
                if isinstance(cur_sel, str) and (cur_sel in metric_axf_ids_ordered):
                    pass
                elif isinstance(last_sel, str) and (last_sel in metric_axf_ids_ordered):
                    st.session_state["selected_metric"] = last_sel
                elif metric_axf_ids_ordered:
                    # Ensure there is always a valid selection.
                    st.session_state.setdefault("selected_metric", metric_axf_ids_ordered[0])

                selected = st.selectbox(
                    "Metric",
                    metric_axf_ids_ordered,
                    key="selected_metric",
                    label_visibility="collapsed",
                    format_func=_fmt_metric,
                )
                st.session_state[_last_sel_key] = selected
            with top2:
                if st.button("＋ New", use_container_width=True):
                    st.session_state["new_metric_mode"] = True
                    # seed defaults once
                    existing = set(metric_axf_ids)
                    st.session_state.setdefault("new_metric_axf_id", metric_create.suggest_axf_id("New metric", existing))
                    st.session_state.setdefault("new_metric_draft", {"name": "New metric"})

            if st.session_state.get("new_metric_mode"):
                st.markdown("**Create metric**")
                existing = set(metric_axf_ids)
                axf_id_new = str(st.session_state.get("new_metric_axf_id") or "").strip()
                axf_id_new = st.text_input("axf_id", value=axf_id_new).strip()
                st.session_state["new_metric_axf_id"] = axf_id_new

                draft0 = st.session_state.get("new_metric_draft")
                if not isinstance(draft0, dict):
                    draft0 = {}

                edited_new = render_metric_form(
                    axf_id=axf_id_new or "newMetric",
                    metric=draft0,
                    capture_types=capture_types,
                    metric_choices=metric_axf_ids_ordered,
                    metric_label_map={},
                )

                cna, cnb, cnc = st.columns([1, 1, 2], vertical_alignment="center")
                with cna:
                    if st.button("Create", type="primary", disabled=(not axf_id_new), use_container_width=True):
                        if axf_id_new in existing:
                            st.error(f"`{axf_id_new}` already exists. Pick a different axf_id.")
                        else:
                            truth_store.save_truth(axf_id_new, edited_new)
                            st.session_state["new_metric_mode"] = False
                            st.session_state.pop("new_metric_draft", None)
                            st.session_state.pop("new_metric_axf_id", None)
                            st.session_state["selected_metric"] = axf_id_new
                            st.success(f"Created `{axf_id_new}`.")
                            st.rerun()
                with cnb:
                    if st.button("Cancel", use_container_width=True):
                        st.session_state["new_metric_mode"] = False
                        st.session_state.pop("new_metric_draft", None)
                        st.session_state.pop("new_metric_axf_id", None)
                        st.rerun()
            else:
                metric = truth_store.load_truth_or_base(selected)

                st.divider()

                # Label map for prompt reference selection
                metric_label_map = {ax: _fmt_metric(ax) for ax in metric_axf_ids_ordered if isinstance(ax, str)}

                edited_metric = render_metric_form(
                    axf_id=selected,
                    metric=metric,
                    capture_types=capture_types,
                    metric_choices=metric_axf_ids_ordered,
                    metric_label_map=metric_label_map,
                )
                # Persist the live draft so the metric dropdown can re-evaluate "*" (incomplete/new)
                # based on the user's in-progress edits (even before saving to truth).
                st.session_state[f"_metric_draft__{selected}"] = edited_metric

                # Build LLM prompt on request (UI lives inside render_metric_form).
                req = st.session_state.pop("_llm_prompt_request", None)
                if isinstance(req, dict) and req.get("axf_id") == selected:
                    # Build capture-config context so the LLM can recommend wiring changes.
                    def _capture_wiring_context(axf_id: str) -> dict[str, Any]:
                        cfg_dir = paths.capture_config_db_dir()
                        out: dict[str, Any] = {
                            "axf_id": axf_id,
                            "priority_capture_types": [],
                            "wired_capture_types": {},
                        }
                        if not isinstance(axf_id, str) or not axf_id.strip() or not cfg_dir.exists():
                            return out

                        for p in sorted(cfg_dir.glob("*.json"), key=lambda x: x.stem.lower()):
                            ct = p.stem
                            try:
                                cfg = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
                            except Exception:
                                continue
                            if not isinstance(cfg, dict):
                                continue

                            # Priority cards
                            pri = cfg.get("metric_priority")
                            in_priority = False
                            if isinstance(pri, list):
                                for it in pri:
                                    if isinstance(it, dict) and it.get("axf_id") == axf_id:
                                        in_priority = True
                                        break
                            if in_priority:
                                out["priority_capture_types"].append(ct)

                            # Wiring
                            dev_keys = cfg.get("device_analytics_keys") if isinstance(cfg.get("device_analytics_keys"), list) else []
                            der_keys = cfg.get("analytics_keys") if isinstance(cfg.get("analytics_keys"), list) else []
                            mp_keys = cfg.get("multi_phase_analytics_keys") if isinstance(cfg.get("multi_phase_analytics_keys"), list) else []
                            phases = cfg.get("phases") if isinstance(cfg.get("phases"), list) else []

                            phase_hits: list[str] = []
                            phase_names: list[str] = []
                            for ph in phases:
                                if not isinstance(ph, dict):
                                    continue
                                pn = ph.get("name") if isinstance(ph.get("name"), str) else ""
                                if pn:
                                    phase_names.append(pn)
                                ks = ph.get("phase_analytics_keys") if isinstance(ph.get("phase_analytics_keys"), list) else []
                                if axf_id in ks and pn:
                                    phase_hits.append(pn)

                            mp_hits: list[dict[str, Any]] = []
                            for ent in mp_keys:
                                if not isinstance(ent, dict):
                                    continue
                                if str(ent.get("key") or "").strip() != axf_id:
                                    continue
                                mp_hits.append(
                                    {
                                        "phase_names": ent.get("phase_names") or [],
                                        "data_set_devices": ent.get("data_set_devices") or [],
                                    }
                                )

                            in_device = axf_id in dev_keys
                            in_derived = axf_id in der_keys
                            has_wiring = bool(in_device or in_derived or phase_hits or mp_hits)
                            if has_wiring:
                                out["wired_capture_types"][ct] = {
                                    "phase_names": phase_names,
                                    "device_analytics": in_device,
                                    "phase_bounded_phases": phase_hits,
                                    "multi_phase_entries": mp_hits,
                                    "capture_level_derived": in_derived,
                                }

                        return out

                    wiring_ctx = _capture_wiring_context(selected)

                    # Include the full scripting reference path (condensed rules are embedded in llm_prompt too).
                    scripting_ref = None
                    try:
                        ref_path = paths.repo_root() / "app" / "analytics" / "analytic" / "SCRIPTING_REFERENCE.md"
                        if ref_path.exists():
                            # Keep it bounded; we include condensed rules in the template anyway.
                            raw = ref_path.read_text(encoding="utf-8", errors="ignore")
                            scripting_ref = raw[:8000].strip()
                    except Exception:
                        scripting_ref = None

                    # Candidate pool: prefer truth versions; fallback to base snapshots.
                    candidates_by_id: dict[str, dict[str, Any]] = {}

                    # 1) base snapshots
                    for bm in base_metrics:
                        ax = bm.get("axf_id")
                        if isinstance(ax, str) and ax:
                            candidates_by_id.setdefault(ax, bm)

                    # 2) truth overrides
                    for ax in metric_create.list_truth_metric_ids():
                        try:
                            candidates_by_id[ax] = truth_store.load_truth_or_base(ax)
                        except Exception:
                            continue

                    mode = str(req.get("mode") or "auto")
                    if mode == "multi_reference":
                        ref_axs = req.get("reference_axf_ids")
                        ref_metrics: list[dict[str, Any]] = []
                        if isinstance(ref_axs, list):
                            for ref_ax in ref_axs:
                                if not isinstance(ref_ax, str) or not ref_ax.strip():
                                    continue
                                rm = candidates_by_id.get(ref_ax)
                                if rm is None:
                                    try:
                                        rm = truth_store.load_truth_or_base(ref_ax)
                                    except Exception:
                                        rm = None
                                if isinstance(rm, dict):
                                    ref_metrics.append(rm)
                        if not ref_metrics:
                            st.error("No reference metrics found.")
                        prompt = llm_prompt.build_multi_reference_prompt(
                            current_metric=edited_metric,
                            reference_metrics=ref_metrics,
                            diff_notes=str(req.get("diff_notes") or ""),
                            wiring_context=wiring_ctx,
                            scripting_reference=scripting_ref,
                        )
                    elif mode == "single_reference":
                        ref_ax = req.get("reference_axf_id")
                        ref_metric = None
                        if isinstance(ref_ax, str) and ref_ax:
                            ref_metric = candidates_by_id.get(ref_ax)
                            if ref_metric is None:
                                try:
                                    ref_metric = truth_store.load_truth_or_base(ref_ax)
                                except Exception:
                                    ref_metric = None
                        if not isinstance(ref_metric, dict):
                            st.error("Reference metric not found.")
                            ref_metric = {}
                        prompt = llm_prompt.build_single_reference_prompt(
                            current_metric=edited_metric,
                            reference_metric=ref_metric,
                            diff_notes=str(req.get("diff_notes") or ""),
                            wiring_context=wiring_ctx,
                            scripting_reference=scripting_ref,
                        )
                    else:
                        candidates = [m for ax, m in candidates_by_id.items() if isinstance(m, dict) and ax != selected]
                        similar = llm_prompt.select_similar_metrics(
                            current_metric=edited_metric,
                            candidates=candidates,
                            k=3,
                        )
                        prompt = llm_prompt.build_prompt(
                            current_metric=edited_metric,
                            similar_metrics=similar,
                            wiring_context=wiring_ctx,
                            scripting_reference=scripting_ref,
                        )
                    st.session_state[f"_llm_prompt_text__{selected}"] = prompt
                    ok, msg = _copy_to_clipboard(prompt)
                    (st.success if ok else st.warning)(msg)
                    # Always provide a UI fallback for manual copy.
                    with st.expander("Last generated prompt (copy manually if needed)", expanded=(not ok)):
                        _browser_copy_button(
                            text=prompt,
                            button_label="Copy prompt (browser)",
                            element_id=f"copy_prompt_{selected}",
                        )
                        st.text_area(
                            "Prompt",
                            value=prompt,
                            height=260,
                            key=f"llm_prompt_preview__{selected}",
                        )

                col_a, col_b = st.columns([0.25, 0.75])
                with col_a:
                    if st.button("Save", type="primary", use_container_width=True):
                        if edited_metric.get("axf_id") not in (None, selected):
                            st.error(f"Save failed: axf_id mismatch (expected '{selected}').")
                        else:
                            truth_store.save_truth(selected, edited_metric)
                            st.success("Saved.")

                    has_base = truth_store.base_metric_path(selected).exists()
                    if st.button(
                        "Reset from base",
                        use_container_width=True,
                        disabled=(not has_base),
                        help=("No base metric exists for this axf_id." if not has_base else None),
                    ):
                        base = truth_store.load_base_metric(selected)
                        truth_store.save_truth(selected, base)
                        st.warning("Reset saved.")
                        st.rerun()

                with col_b:
                    with st.expander("Raw JSON (read-only preview)", expanded=False):
                        st.code(_pretty_json(edited_metric), language="json")


if __name__ == "__main__":
    main()

