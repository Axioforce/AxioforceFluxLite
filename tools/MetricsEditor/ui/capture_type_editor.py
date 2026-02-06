from __future__ import annotations

import json
import html
from collections import Counter
from pathlib import Path
from typing import Any

import streamlit as st

from tools.MetricsEditor import metric_create, paths, truth_store


def _load_capture_config(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, f"Capture config not found: {path.name}"
    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        if not isinstance(obj, dict):
            return None, "Capture config JSON root must be an object."
        return obj, None
    except Exception as e:
        return None, f"Failed to load capture config: {e}"


def _save_capture_config(path: Path, cfg: dict[str, Any]) -> str | None:
    try:
        path.write_text(json.dumps(cfg, indent=4, ensure_ascii=False), encoding="utf-8")
        return None
    except Exception as e:
        return f"Failed to save capture config: {e}"


def _unique_keep_order(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for x in items:
        x = (x or "").strip()
        if not x or x in seen:
            continue
        out.append(x)
        seen.add(x)
    return out


def _csv_list(s: str) -> list[str]:
    # Accept commas + newlines
    items: list[str] = []
    for part in (s or "").replace("\n", ",").split(","):
        t = part.strip()
        if t:
            items.append(t)
    return _unique_keep_order(items)


def _metric_name_map(base_metrics: list[dict[str, Any]]) -> dict[str, str]:
    """
    Build axf_id -> display name map from:
    - analytics_db snapshots (base_metrics)
    - truth-only metrics (file_system/metrics_truth/*.json)
    """
    axf_to_name: dict[str, str] = {
        m.get("axf_id"): m.get("name")
        for m in base_metrics
        if isinstance(m.get("axf_id"), str) and isinstance(m.get("name"), str)
    }
    for ax in metric_create.list_truth_metric_ids():
        if not isinstance(ax, str) or not ax or ax in axf_to_name:
            continue
        try:
            m2 = truth_store.load_truth_or_base(ax)
            nm = m2.get("name")
            if isinstance(nm, str) and nm.strip():
                axf_to_name[ax] = nm.strip()
            else:
                axf_to_name[ax] = ax
        except Exception:
            axf_to_name[ax] = ax
    return {k: v for k, v in axf_to_name.items() if isinstance(k, str) and k}


def render_capture_type_editor(*, capture_types: list[str], base_metrics: list[dict[str, Any]]) -> str:
    """
    Render a capture-type panel for a capture config snapshot:
      - Execution wiring (what runs): phase/device/multi-phase/capture analytics keys
      - Priority list (what to surface first): ordered metric_priority

    File: `file_system/capture_config_from_db/<ct>.json`.
    """
    options = [""] + list(capture_types)
    selected = st.selectbox(
        "Capture type",
        options=options,
        index=0,
        key="selected_capture_type",
        label_visibility="collapsed",
    )
    if not selected:
        st.caption("Select a capture type to view/edit its priority metrics.")
        return ""

    axf_to_name = _metric_name_map(base_metrics)
    all_metric_ids = sorted(axf_to_name.keys(), key=lambda a: axf_to_name.get(a, a).lower())

    cfg_path = paths.capture_config_db_dir() / f"{selected}.json"
    cfg, err = _load_capture_config(cfg_path)
    if err:
        st.error(err)
        return selected
    assert cfg is not None

    mode = st.radio(
        "Editor mode",
        options=["Simple", "Advanced (raw JSON)"],
        index=0,
        horizontal=True,
        help=(
            "Simple mode edits metric cards (priority list) and compute wiring together, with guardrails. "
            "Advanced mode lets you edit the full JSON as-is."
        ),
        key=f"ct_mode__{selected}",
    )

    if mode == "Advanced (raw JSON)":
        st.caption("Edits apply only when the JSON parses successfully.")
        raw = st.text_area("Capture config JSON", value=json.dumps(cfg, indent=4, ensure_ascii=False), height=520)
        if st.button("Save raw JSON", type="primary", use_container_width=True, key=f"ct_save_raw__{selected}"):
            try:
                obj = json.loads(raw)
                if not isinstance(obj, dict):
                    st.error("JSON root must be an object.")
                else:
                    save_err = _save_capture_config(cfg_path, obj)
                    if save_err:
                        st.error(save_err)
                    else:
                        st.success("Saved.")
                        st.rerun()
            except Exception as e:
                st.error(f"JSON parse error: {e}")
        return selected

    # ---- Simple mode: Cards + wiring ----
    cfg.setdefault("analytics_keys", [])
    cfg.setdefault("device_analytics_keys", [])
    cfg.setdefault("multi_phase_analytics_keys", [])
    cfg.setdefault("phases", [])
    cfg.setdefault("metric_priority", [])

    phases = cfg.get("phases") if isinstance(cfg.get("phases"), list) else []
    phase_names = [p.get("name") for p in phases if isinstance(p, dict) and isinstance(p.get("name"), str)]
    phase_names = [x for x in phase_names if x]

    # Build compute wiring state (from cfg)
    device_keys = set([str(x) for x in (cfg.get("device_analytics_keys") or []) if str(x).strip()])
    derived_keys = set([str(x) for x in (cfg.get("analytics_keys") or []) if str(x).strip()])

    phase_key_map: dict[str, set[str]] = {pn: set() for pn in phase_names}
    for ph in phases:
        if not isinstance(ph, dict):
            continue
        pn = ph.get("name") if isinstance(ph.get("name"), str) else ""
        if pn and pn not in phase_key_map:
            phase_key_map[pn] = set()
        keys = ph.get("phase_analytics_keys") if isinstance(ph.get("phase_analytics_keys"), list) else []
        for k in keys:
            if isinstance(k, str) and k.strip() and pn:
                phase_key_map[pn].add(k.strip())

    raw_mp = cfg.get("multi_phase_analytics_keys") if isinstance(cfg.get("multi_phase_analytics_keys"), list) else []
    mp_entries: list[dict[str, Any]] = [dict(x) for x in raw_mp if isinstance(x, dict)]
    mp_by_key: dict[str, list[dict[str, Any]]] = {}
    for ent in mp_entries:
        k = ent.get("key")
        if isinstance(k, str) and k.strip():
            mp_by_key.setdefault(k.strip(), []).append(ent)

    # If the capture config wiring changed on disk (or we saved), refresh the wiring widget state
    # so the UI always reflects what's in the JSON after switching capture types / reloading.
    def _wiring_sig() -> str:
        phase_sig = {pn: sorted(list(ks)) for pn, ks in phase_key_map.items()}
        mp_sig = []
        for k in sorted(mp_by_key.keys()):
            for ent in mp_by_key.get(k, []):
                if not isinstance(ent, dict):
                    continue
                mp_sig.append(
                    {
                        "key": k,
                        "phase_names": [str(x) for x in (ent.get("phase_names") or []) if str(x).strip()],
                        "data_set_devices": [str(x) for x in (ent.get("data_set_devices") or []) if str(x).strip()],
                    }
                )
        return json.dumps(
            {
                "device": sorted(list(device_keys)),
                "derived": sorted(list(derived_keys)),
                "phase": phase_sig,
                "multi_phase": mp_sig,
            },
            sort_keys=True,
            ensure_ascii=False,
        )

    sig_key = f"ct_wiring_sig__{selected}"
    sig = _wiring_sig()
    if st.session_state.get(sig_key) != sig:
        # Clear only wiring-related widget state for this capture type (not cards).
        prefix = f"__{selected}__"
        for k in list(st.session_state.keys()):
            if (
                isinstance(k, str)
                and prefix in k
                and (
                    k.startswith("ct_w_dev__")
                    or k.startswith("ct_w_der__")
                    or k.startswith("ct_w_phases__")
                    or k.startswith("ct_w_phase_toggle__")
                    or k.startswith("ct_w_mp__")
                )
            ):
                st.session_state.pop(k, None)
        st.session_state[sig_key] = sig

    # Cards (priority list) - keep duplicates, preserve order
    cards_key = f"ct_cards__{selected}"
    if cards_key not in st.session_state:
        raw_cards = cfg.get("metric_priority") if isinstance(cfg.get("metric_priority"), list) else []
        st.session_state[cards_key] = [dict(x) for x in raw_cards if isinstance(x, dict) and isinstance(x.get("axf_id"), str)]
    cards: list[dict[str, Any]] = st.session_state.get(cards_key, [])
    if not isinstance(cards, list):
        cards = []
        st.session_state[cards_key] = cards

    # Metrics wired for compute but missing from cards
    card_ids = [c.get("axf_id") for c in cards if isinstance(c, dict) and isinstance(c.get("axf_id"), str)]
    card_ids_set = {x for x in card_ids if isinstance(x, str) and x.strip()}
    computed_ids: set[str] = set(device_keys) | set(derived_keys) | set(mp_by_key.keys())
    for pn, ks in phase_key_map.items():
        computed_ids |= set(ks)
    computed_only = sorted([x for x in computed_ids if x not in card_ids_set], key=lambda a: axf_to_name.get(a, a).lower())

    st.markdown("### Key metric cards (display order)")
    st.caption(
        "These are the ordered items the UI can surface first (`metric_priority`). "
        "Each card can reference a specific axis/phase/device slice, but compute wiring is configured separately."
    )

    add1, add2 = st.columns([0.82, 0.18], vertical_alignment="center")
    with add1:
        to_add = st.selectbox(
            "Add card",
            options=[""] + all_metric_ids,
            index=0,
            key=f"ct_cards_add__{selected}",
            label_visibility="collapsed",
        )
    with add2:
        if st.button("＋ Add", disabled=(not to_add), use_container_width=True, key=f"ct_cards_add_btn__{selected}"):
            cards.append({"axf_id": to_add, "axis": None, "phase": None, "device": None})
            st.session_state[cards_key] = cards
            st.rerun()

    if not cards:
        st.info("No cards yet. Add a metric above.")
    else:
        moved_key = f"ct_cards_moved__{selected}"
        moved_idx = st.session_state.get(moved_key) if isinstance(st.session_state.get(moved_key), int) else -1
        clear_moved = False
        # Avoid Streamlit duplicate keys: only render compute-wiring widgets once per metric key.
        first_card_index_for_metric: dict[str, int] = {}
        # Extra safety: even if wiring render is accidentally invoked twice in one run (e.g. due to
        # future refactors), short-circuit to prevent StreamlitDuplicateElementKey crashes.
        rendered_wiring_for_metric: set[str] = set()

        # For duplicate cards, show what differentiates each "view" (axis/phase/device)
        raw_axs: list[str] = []
        for c in cards:
            ax0 = c.get("axf_id") if isinstance(c, dict) else None
            if isinstance(ax0, str) and ax0.strip():
                raw_axs.append(ax0.strip())
        ax_counts: Counter[str] = Counter(raw_axs)
        ax_seen: dict[str, int] = {}

        def _card_view_html(*, ax: str, card: dict[str, Any]) -> str:
            """
            Human-friendly label shown in the card list.

            Goal: make duplicates scannable by surfacing the slice (axis/phase/device).
            """
            name = (axf_to_name.get(ax) or "").strip()
            # Keep the list scannable: hide axf_id when we have a human name.
            # (axf_id is still available in the card expander + metric editor.)
            base = html.escape(name if name else ax)

            axis = (card.get("axis") if isinstance(card.get("axis"), str) else None) or None
            phase = (card.get("phase") if isinstance(card.get("phase"), str) else None) or None
            device = (card.get("device") if isinstance(card.get("device"), str) else None) or None

            parts: list[str] = []
            if axis and axis.strip():
                parts.append(html.escape(axis.strip()))
            if phase and phase.strip():
                parts.append(html.escape(phase.strip()))
            if device and device.strip():
                parts.append(html.escape(device.strip()))

            # If metric appears multiple times, mark which view this is.
            total = ax_counts.get(ax, 0)
            if total > 1:
                n = ax_seen.get(ax, 0) + 1
                ax_seen[ax] = n
                parts.append(html.escape(f"view {n}/{total}"))

            if parts:
                meta = " · ".join(parts)
                return f"<span class='ctName'>{base}</span><span class='ctMeta'> · {meta}</span>"
            return f"<span class='ctName'>{base}</span>"

        def _wire_defaults(ax: str) -> None:
            # Initialize Streamlit widget defaults once per metric key
            st.session_state.setdefault(f"ct_w_dev__{selected}__{ax}", ax in device_keys)
            st.session_state.setdefault(f"ct_w_der__{selected}__{ax}", ax in derived_keys)
            in_phases = [pn for pn, ks in phase_key_map.items() if ax in ks]
            st.session_state.setdefault(f"ct_w_phases__{selected}__{ax}", in_phases)
            st.session_state.setdefault(f"ct_w_phase_toggle__{selected}__{ax}", bool(in_phases))
            st.session_state.setdefault(f"ct_w_mp__{selected}__{ax}", mp_by_key.get(ax, []))

        def _render_wiring_editor(ax: str, *, uid: str) -> None:
            rk = f"{selected}::{ax}"
            if rk in rendered_wiring_for_metric:
                st.info(
                    "Compute wiring is shared per metric key and is already rendered elsewhere on this page. "
                    "Scroll up to the first card instance for this metric."
                )
                return
            rendered_wiring_for_metric.add(rk)
            _wire_defaults(ax)
            st.markdown("**Compute wiring (what runs)**")
            st.caption(
                "This section affects the data passed to the script. "
                "If you change wiring here, it applies to the metric key everywhere (not just this card)."
            )

            w1, w2, w3 = st.columns([0.34, 0.33, 0.33], vertical_alignment="center")
            with w1:
                dev_key = f"ct_w_dev__{selected}__{ax}"
                dev_wkey = f"{dev_key}__w{uid}"
                st.session_state.setdefault(dev_wkey, bool(st.session_state.get(dev_key, ax in device_keys)))
                in_device = st.checkbox(
                    "Capture-wide (device analytics)",
                    key=dev_wkey,
                    help="Runs over the whole capture per device (raw DataFrame).",
                )
                st.session_state[dev_key] = bool(in_device)
            with w2:
                ph_t_key = f"ct_w_phase_toggle__{selected}__{ax}"
                ph_t_wkey = f"{ph_t_key}__w{uid}"
                st.session_state.setdefault(ph_t_wkey, bool(st.session_state.get(ph_t_key, False)))
                in_phase = st.checkbox(
                    "Phase-bounded",
                    key=ph_t_wkey,
                    help="Runs inside selected phases only (raw DataFrame).",
                )
                st.session_state[ph_t_key] = bool(in_phase)
            with w3:
                der_key = f"ct_w_der__{selected}__{ax}"
                der_wkey = f"{der_key}__w{uid}"
                st.session_state.setdefault(der_wkey, bool(st.session_state.get(der_key, ax in derived_keys)))
                in_derived = st.checkbox(
                    "Capture-level derived",
                    key=der_wkey,
                    help="Runs once per capture (often Parent). May use required_metrics if provided, but can also use raw capture-level data.",
                )
                st.session_state[der_key] = bool(in_derived)

            if in_phase:
                ph_key = f"ct_w_phases__{selected}__{ax}"
                ph_wkey = f"{ph_key}__w{uid}"
                st.session_state.setdefault(ph_wkey, list(st.session_state.get(ph_key, [])))
                ph_sel = st.multiselect(
                    "Phases to run in",
                    options=phase_names,
                    default=list(st.session_state.get(ph_key, [])),
                    key=ph_wkey,
                    help="If the script assumes it is already Landing-only, wire it to the Landing phase here.",
                )
                st.session_state[ph_key] = list(ph_sel) if isinstance(ph_sel, list) else []
            # Note: when Phase-bounded is unchecked, we *do not* clear the selected phases here.
            # Clearing is handled by Save logic (it ignores phases unless Phase-bounded is enabled),
            # so users can toggle off/on without losing their phase selections.

            # Multi-phase entries (advanced but still in simple mode)
            st.markdown("**Multi-phase entries**")
            st.caption("Optional. Use when you need a metric over multiple phases combined. Data is filtered per device.")
            mp_key2 = f"ct_w_mp__{selected}__{ax}"
            mp_list2 = st.session_state.get(mp_key2, [])
            if not isinstance(mp_list2, list):
                mp_list2 = []
            mp_add_c1, mp_add_c2 = st.columns([0.86, 0.14], vertical_alignment="center")
            with mp_add_c2:
                if st.button("＋", key=f"ct_w_mp_add__{selected}__{ax}__w{uid}"):
                    mp_list2.append({"key": ax, "phase_names": [], "data_set_devices": []})
                    st.session_state[mp_key2] = mp_list2
                    st.rerun()
            if mp_list2:
                for j, ent in enumerate(list(mp_list2)):
                    if not isinstance(ent, dict):
                        continue
                    ptxt = ", ".join([str(x) for x in (ent.get("phase_names") or []) if str(x).strip()])
                    dtxt = ", ".join([str(x) for x in (ent.get("data_set_devices") or []) if str(x).strip()])
                    c1, c2, c3 = st.columns([0.44, 0.48, 0.08], vertical_alignment="center")
                    with c1:
                        ph_in_key = f"ct_w_mp_phases__{selected}__{ax}__{j}__w{uid}"
                        st.session_state.setdefault(ph_in_key, ptxt)
                        phases_in = st.text_input(
                            "phase_names (csv)",
                            value=ptxt,
                            key=ph_in_key,
                            help=("Available phases: " + ", ".join(phase_names)) if phase_names else None,
                        )
                    with c2:
                        dev_in_key = f"ct_w_mp_devices__{selected}__{ax}__{j}__w{uid}"
                        st.session_state.setdefault(dev_in_key, dtxt)
                        devices_in = st.text_input(
                            "data_set_devices (csv)",
                            value=dtxt,
                            key=dev_in_key,
                            help="Common: Parent, Left, Right (depends on capture).",
                        )
                    with c3:
                        if st.button("🗑", key=f"ct_w_mp_del__{selected}__{ax}__{j}__w{uid}"):
                            mp_list2 = [x for jj, x in enumerate(mp_list2) if jj != j]
                            st.session_state[mp_key2] = mp_list2
                            st.rerun()
                    ent["key"] = ax
                    ent["phase_names"] = _csv_list(phases_in)
                    ent["data_set_devices"] = _csv_list(devices_in)
                    mp_list2[j] = ent
                st.session_state[mp_key2] = mp_list2
            else:
                st.caption("No multi-phase entries for this metric.")

            # Script implications (always show)
            st.markdown("**Script implications (quick rules)**")
            bullets: list[str] = []
            if in_derived:
                bullets.append("Capture-level derived: script should NOT assume pandas DataFrame; it may receive dict-of-lists.")
            if in_device or in_phase:
                bullets.append("Raw-DataFrame scope: script can use pandas operations and columns like time/Fx/Fz/etc.")
            bullets.append("Cross-device comparisons (Left/Right, Landing Zone, etc.) require metric.required_devices to include 'all' so data has position_id.")
            st.write("\n".join([f"- {b}" for b in bullets]))

        for i, card in enumerate(list(cards)):
            if not isinstance(card, dict):
                continue
            ax = card.get("axf_id")
            if not isinstance(ax, str) or not ax.strip():
                continue
            ax = ax.strip()
            nm = axf_to_name.get(ax, "")
            label = _card_view_html(ax=ax, card=card)
            badge = f"#{i + 1}"
            # Keep the card text on the left and cluster action buttons on the far right.
            # This avoids "floating" buttons in the middle when the row is wide.
            left, right = st.columns([0.82, 0.18], vertical_alignment="center")
            with left:
                cls = "ctRow ctMoved" if (i == moved_idx) else "ctRow"
                if i == moved_idx:
                    clear_moved = True
                st.markdown(
                    f"<div class='{cls}'><span class='ctIdx'>{badge}</span><span class='ctLabel'>{label}</span></div>",
                    unsafe_allow_html=True,
                )
            with right:
                b1, b2, b3 = st.columns([1, 1, 1], gap="small", vertical_alignment="center")
                with b1:
                    if st.button("↑", disabled=(i == 0), key=f"ct_card_up__{selected}__{i}"):
                        cards[i - 1], cards[i] = cards[i], cards[i - 1]
                        st.session_state[cards_key] = cards
                        st.session_state[moved_key] = i - 1
                        st.rerun()
                with b2:
                    if st.button("↓", disabled=(i >= len(cards) - 1), key=f"ct_card_dn__{selected}__{i}"):
                        cards[i + 1], cards[i] = cards[i], cards[i + 1]
                        st.session_state[cards_key] = cards
                        st.session_state[moved_key] = i + 1
                        st.rerun()
                with b3:
                    if st.button("🗑", key=f"ct_card_del__{selected}__{i}"):
                        cards = [c for j, c in enumerate(cards) if j != i]
                        st.session_state[cards_key] = cards
                        st.rerun()

            with st.expander("Edit", expanded=False):
                st.markdown("**Card metadata (what to highlight)**")
                cmeta1, cmeta2, cmeta3 = st.columns(3, gap="medium")
                with cmeta1:
                    axis = st.text_input("axis (component)", value=str(card.get("axis") or ""), key=f"ct_card_axis__{selected}__{i}")
                with cmeta2:
                    phase = st.text_input("phase (label)", value=str(card.get("phase") or ""), key=f"ct_card_phase__{selected}__{i}")
                with cmeta3:
                    device = st.text_input("device (label)", value=str(card.get("device") or ""), key=f"ct_card_device__{selected}__{i}")
                card["axis"] = axis.strip() or None
                card["phase"] = phase.strip() or None
                card["device"] = device.strip() or None
                cards[i] = card
                st.session_state[cards_key] = cards

                st.divider()
                if ax not in first_card_index_for_metric:
                    first_card_index_for_metric[ax] = i
                    _render_wiring_editor(ax, uid=str(i))
                else:
                    first_idx = first_card_index_for_metric[ax]
                    st.info(
                        f"Compute wiring is shared per metric key. "
                        f"To avoid duplicate widget keys, wiring controls are shown only on the first card for `{ax}` "
                        f"(card #{first_idx + 1})."
                    )

        if clear_moved:
            st.session_state.pop(moved_key, None)

    with st.expander(f"Non-Key Metrics Wiring: {len(computed_only)}", expanded=True):
        st.caption("These metrics are wired to compute but are not in the priority cards list. You can edit their wiring here.")
        if not computed_only:
            st.caption("None.")
        else:
            for i, ax in enumerate(computed_only):
                st.markdown(f"### {axf_to_name.get(ax, ax)} ({ax})")
                _render_wiring_editor(ax, uid=f"other_{i}")
                st.divider()
        # Also show the list with add card buttons
        st.markdown("**Add to cards**")
        for ax in computed_only:
            nm = axf_to_name.get(ax, "")
            label = f"{nm}  ({ax})" if nm else ax
            c1, c2 = st.columns([0.80, 0.20], vertical_alignment="center")
            with c1:
                st.markdown(f"- {label}")
            with c2:
                if st.button("Add card", key=f"ct_add_card_from_other__{selected}__{ax}"):
                    cards.append({"axf_id": ax, "axis": None, "phase": None, "device": None})
                    st.session_state[cards_key] = cards
                    st.rerun()

    st.divider()
    if st.button("Save cards + wiring", type="primary", use_container_width=True, key=f"ct_save_all__{selected}"):
        # 1) Save metric_priority (cards order; keep duplicates)
        cfg["metric_priority"] = [
            {"axf_id": str(c.get("axf_id")), "axis": c.get("axis"), "phase": c.get("phase"), "device": c.get("device")}
            for c in (st.session_state.get(cards_key) or [])
            if isinstance(c, dict) and isinstance(c.get("axf_id"), str) and str(c.get("axf_id")).strip()
        ]

        # 2) Save compute wiring (union across metrics)
        # Start with existing wiring, then update for key metrics (card_ids_set)
        new_device_keys: set[str] = set(device_keys)
        new_derived_keys: set[str] = set(derived_keys)
        new_phase_map: dict[str, set[str]] = {pn: set(phase_key_map.get(pn, [])) for pn in phase_names}
        new_mp_entries: list[dict[str, Any]] = list(mp_entries)  # Preserve existing

        # Determine the metric universe from:
        # - metrics in cards
        # - metrics previously wired (computed_only)
        universe = sorted({*card_ids_set, *computed_ids})

        for ax in universe:
            if not isinstance(ax, str) or not ax.strip():
                continue
            # Only update wiring for metrics with UI widgets (key + non-key with wiring UI)
            if f"ct_w_dev__{selected}__{ax}" in st.session_state:
                if st.session_state.get(f"ct_w_dev__{selected}__{ax}", False):
                    new_device_keys.add(ax)
                else:
                    new_device_keys.discard(ax)
                if st.session_state.get(f"ct_w_der__{selected}__{ax}", False):
                    new_derived_keys.add(ax)
                else:
                    new_derived_keys.discard(ax)
                # Reset phase assignments for this metric, then re-add if selected
                for pn in new_phase_map:
                    new_phase_map[pn].discard(ax)
                # Only persist phase selections when Phase-bounded is enabled.
                if st.session_state.get(f"ct_w_phase_toggle__{selected}__{ax}", False):
                    phs = st.session_state.get(f"ct_w_phases__{selected}__{ax}", [])
                    if isinstance(phs, list):
                        for pn in phs:
                            if isinstance(pn, str) and pn in new_phase_map:
                                new_phase_map[pn].add(ax)
                # Update multi-phase for this metric
                mp_list2 = st.session_state.get(f"ct_w_mp__{selected}__{ax}", [])
                # Remove existing entries for this key
                new_mp_entries = [ent for ent in new_mp_entries if ent.get("key") != ax]
                if isinstance(mp_list2, list):
                    for ent in mp_list2:
                        if not isinstance(ent, dict):
                            continue
                        k = str(ent.get("key") or "").strip()
                        if not k:
                            continue
                        new_mp_entries.append(
                            {
                                "key": k,
                                "phase_names": [str(x) for x in (ent.get("phase_names") or []) if str(x).strip()],
                                "data_set_devices": [str(x) for x in (ent.get("data_set_devices") or []) if str(x).strip()],
                            }
                        )

        cfg["device_analytics_keys"] = sorted(new_device_keys)
        cfg["analytics_keys"] = sorted(new_derived_keys)
        cfg["multi_phase_analytics_keys"] = new_mp_entries

        # phases: preserve phase dicts, only overwrite phase_analytics_keys
        new_phases_cfg: list[dict[str, Any]] = []
        for ph in phases:
            if not isinstance(ph, dict):
                continue
            pn = ph.get("name") if isinstance(ph.get("name"), str) else ""
            ph2 = dict(ph)
            if pn and pn in new_phase_map:
                ph2["phase_analytics_keys"] = sorted(new_phase_map[pn])
            new_phases_cfg.append(ph2)
        cfg["phases"] = new_phases_cfg

        save_err = _save_capture_config(cfg_path, cfg)
        if save_err:
            st.error(save_err)
        else:
            st.success("Saved.")
            st.rerun()

    return selected

