from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.MetricsEditor import paths


def _read_json(p: Path) -> dict[str, Any]:
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_CAMEL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# Common abbreviations used in docs that should resolve to Dynamo metric names.
# Keep this conservative; it's only used for matching (not displayed).
_TOKEN_EXPANSIONS: dict[str, list[str]] = {
    "rfd": ["rate", "force", "development"],
    "avg": ["average"],
    "cmj": ["countermovement", "jump"],
    "com": ["center", "of", "mass"],
    "rsi": ["reactive", "strength", "index"],
    "mrsi": ["modified", "reactive", "strength", "index"],
    "m-rsi": ["modified", "reactive", "strength", "index"],
    "l/r": ["left", "right"],
    "r/l": ["right", "left"],
    "lr": ["left", "right"],
    "rl": ["right", "left"],
}

# Pre-normalization substitutions for multi-token shorthands.
_TEXT_SUBS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bL\s*/\s*R\b", re.IGNORECASE), "left right"),
    (re.compile(r"\bR\s*/\s*L\b", re.IGNORECASE), "right left"),
    (re.compile(r"\bm\s*R\s*S\s*I\b", re.IGNORECASE), "modified reactive strength index"),
    (re.compile(r"\bR\s*S\s*I\b", re.IGNORECASE), "reactive strength index"),
    (re.compile(r"\bR\s*F\s*D\b", re.IGNORECASE), "rate force development"),
    (re.compile(r"\bC\s*O\s*M\b", re.IGNORECASE), "center of mass"),
]


def normalize_name(name: str) -> str:
    s = (name or "").strip().lower()
    s = _NON_ALNUM.sub(" ", s)
    s = " ".join(s.split())
    return s


def tokenize(text: str) -> list[str]:
    raw = (text or "").strip()
    for pat, repl in _TEXT_SUBS:
        raw = pat.sub(repl, raw)

    s = normalize_name(raw)
    if not s:
        return []

    out: list[str] = []
    for tok in s.split(" "):
        exp = _TOKEN_EXPANSIONS.get(tok)
        if exp:
            out.extend(exp)
        else:
            out.append(tok)
    return [t for t in out if t]


def tokenize_axf_id(axf_id: str) -> list[str]:
    """
    Split an axf_id like 'positiveNetImpulse' into tokens:
      ['positive', 'net', 'impulse']
    """
    if not axf_id:
        return []
    # Insert spaces before capitals, then normalize
    split = _CAMEL_SPLIT.sub(" ", axf_id)
    return tokenize(split)


def token_signature(tokens: list[str]) -> str:
    # Order-insensitive signature for "same words, different order" matching
    return "|".join(sorted([t for t in tokens if t]))


@dataclass(frozen=True)
class MetricStub:
    axf_id: str
    name: str
    name_sig: str
    id_sig: str


def load_all_base_metrics() -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    for p in sorted(paths.analytics_db_dir().glob("*.json")):
        try:
            metrics.append(_read_json(p))
        except Exception:
            # keep the editor resilient; bad files can be handled manually
            continue
    return metrics


@dataclass(frozen=True)
class MetricIndex:
    by_name_norm: dict[str, list[MetricStub]]
    by_token_sig: dict[str, list[MetricStub]]
    by_id_token_sig: dict[str, list[MetricStub]]
    all_metrics: list[MetricStub]


def build_metric_index(metrics: list[dict[str, Any]]) -> MetricIndex:
    by_name_norm: dict[str, list[MetricStub]] = {}
    by_token_sig: dict[str, list[MetricStub]] = {}
    by_id_token_sig: dict[str, list[MetricStub]] = {}
    all_metrics: list[MetricStub] = []

    for m in metrics:
        axf_id = m.get("axf_id")
        name = m.get("name")
        if not isinstance(axf_id, str) or not isinstance(name, str):
            continue

        name_tokens = tokenize(name)
        id_tokens = tokenize_axf_id(axf_id)

        stub = MetricStub(
            axf_id=axf_id,
            name=name,
            name_sig=token_signature(name_tokens),
            id_sig=token_signature(id_tokens),
        )

        all_metrics.append(stub)
        by_name_norm.setdefault(normalize_name(name), []).append(stub)
        by_token_sig.setdefault(stub.name_sig, []).append(stub)
        by_id_token_sig.setdefault(stub.id_sig, []).append(stub)

    return MetricIndex(
        by_name_norm=by_name_norm,
        by_token_sig=by_token_sig,
        by_id_token_sig=by_id_token_sig,
        all_metrics=all_metrics,
    )


def build_name_index(metrics: list[dict[str, Any]]) -> dict[str, list[MetricStub]]:
    """
    Backwards-compatible wrapper for older callers.

    Returns the original index type (normalized metric name -> stubs).
    New code should prefer `build_metric_index`.
    """
    return build_metric_index(metrics).by_name_norm


def resolve_metric_axf_id(
    metric_name: str,
    index: MetricIndex,
    manual_map: dict[str, str | None] | None = None,
) -> tuple[str | None, str | None]:
    """
    Returns (axf_id, warning). warning is non-None when resolution is ambiguous.
    """
    name_tokens = tokenize(metric_name)
    sig = token_signature(name_tokens)

    # 0) user-provided override (order-insensitive)
    if manual_map and sig in manual_map:
        v = manual_map[sig]
        # Explicit user "unmap" (store null in JSON) forces unresolved.
        if v is None:
            return None, "manual_unmapped"
        return v, "manual_map"

    # 1) exact name match (ignores spacing/case/punct)
    key = normalize_name(metric_name)
    cands = index.by_name_norm.get(key, [])
    if not cands:
        # 2) order-insensitive name match (same words in different order)
        cands = index.by_token_sig.get(sig, [])

    if not cands:
        # 3) order-insensitive match against axf_id tokenization (camelCase)
        cands = index.by_id_token_sig.get(sig, [])

    if len(cands) == 1:
        return cands[0].axf_id, None
    if len(cands) > 1:
        return cands[0].axf_id, "ambiguous_name"

    # 4) fuzzy token overlap fallback
    # Score by Jaccard similarity against name tokens and id tokens; pick best above threshold.
    query_set = set(name_tokens)
    if not query_set:
        return None, "no_match"

    best: tuple[float, MetricStub] | None = None
    runner_up: tuple[float, MetricStub] | None = None

    for stub in index.all_metrics:
        name_set = set(stub.name_sig.split("|")) if stub.name_sig else set()
        id_set = set(stub.id_sig.split("|")) if stub.id_sig else set()

        def jacc(a: set[str], b: set[str]) -> float:
            if not a or not b:
                return 0.0
            return len(a & b) / len(a | b)

        score = max(jacc(query_set, name_set), jacc(query_set, id_set))
        if best is None or score > best[0]:
            runner_up = best
            best = (score, stub)
        elif runner_up is None or score > runner_up[0]:
            runner_up = (score, stub)

    if best is None or best[0] < 0.60:
        return None, "no_match"

    # If very close second-best, call it ambiguous
    if runner_up is not None and (best[0] - runner_up[0]) < 0.05:
        return best[1].axf_id, "ambiguous_fuzzy"

    return best[1].axf_id, "fuzzy_match"

