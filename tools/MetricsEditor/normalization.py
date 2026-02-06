from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Optional


CHOICES: list[str] = [
    "Maximize",
    "Minimize",
    "Abs Maximize",
    "Abs Minimize",
    "Target",
]


_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("’", "'")
    s = _NON_ALNUM.sub(" ", s)
    return " ".join(s.split())


def _has_fuzzy_token(text: str, token: str, *, cutoff: float = 0.82) -> bool:
    """
    Cheap fuzzy match against individual tokens so we can handle things like:
    - "incrase" -> increase
    - "decrese" -> decrease
    """
    t = _norm(text)
    if not t:
        return False
    tok = _norm(token)
    if tok in t.split():
        return True
    for w in t.split():
        if SequenceMatcher(a=w, b=tok).ratio() >= cutoff:
            return True
    return False


def normalize_optimization_mode(raw: str | None) -> Optional[str]:
    """
    Normalize a free-form optimization direction/mode into one of:

    - Maximize
    - Minimize
    - Abs Maximize
    - Abs Minimize
    - Target
    - None (meaning "unset"/null)

    Rules (as requested):
    - increase -> Maximize
    - decrease -> Minimize
    - none -> None
    - ignore case, tolerate fuzzy matches
    """
    if raw is None:
        return None

    s = _norm(raw)
    if not s:
        return None

    # Explicit "none"/null-ish
    if s in {"none", "null", "na", "n a", "n/a", "no", "unset"}:
        return None

    # Direct canonical values
    for c in CHOICES:
        if s == _norm(c):
            return c

    # Target (priority)
    if any(_has_fuzzy_token(s, w) for w in ("target", "goal", "aim", "objective", "closest")):
        return "Target"

    # Abs?
    is_abs = any(_has_fuzzy_token(s, w) for w in ("abs", "absolute", "magnitude", "modulus"))

    # Increase/decrease synonyms
    is_inc = any(_has_fuzzy_token(s, w) for w in ("increase", "maximize", "maximise", "higher", "largest", "maximum", "up", "raise"))
    is_dec = any(_has_fuzzy_token(s, w) for w in ("decrease", "minimize", "minimise", "lower", "smallest", "minimum", "down", "reduce"))

    if is_abs and is_inc:
        return "Abs Maximize"
    if is_abs and is_dec:
        return "Abs Minimize"
    if is_inc:
        return "Maximize"
    if is_dec:
        return "Minimize"

    # If we can't classify, leave it unset (user can choose).
    return None

