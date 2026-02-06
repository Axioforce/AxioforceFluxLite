from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


@dataclass(frozen=True)
class DeclaredMetric:
    name: str
    units: str | None
    equation: str
    description: str | None
    how_to_use: str | None


_DECLARE_RE = re.compile(r"\\DeclareMetric\s*\{", re.MULTILINE)


def _parse_braced_arg(s: str, start_idx: int) -> tuple[str, int]:
    """
    Parse a { ... } argument with balanced braces. Returns (content, next_index).
    start_idx must point to the character right after the opening '{'.
    """
    depth = 1
    i = start_idx
    out: list[str] = []
    while i < len(s):
        ch = s[i]
        if ch == "{":
            depth += 1
            out.append(ch)
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return "".join(out).strip(), i + 1
            out.append(ch)
        else:
            out.append(ch)
        i += 1
    raise ValueError("Unbalanced braces while parsing DeclareMetric")


def parse_declaremetric_blocks(tex: str) -> list[DeclaredMetric]:
    """
    Extracts \\DeclareMetric{name}{units}{equation}{description}{how_to_use}
    from the provided .tex content.

    Notes:
    - Full-line comments (lines whose first non-whitespace character is '%') are ignored.
    """
    tex = "\n".join([ln for ln in tex.splitlines() if not ln.lstrip().startswith("%")])

    results: list[DeclaredMetric] = []
    for m in _DECLARE_RE.finditer(tex):
        i = m.end()  # points after the opening '{' of arg1
        name, i = _parse_braced_arg(tex, i)
        # skip whitespace and expect '{'
        while i < len(tex) and tex[i].isspace():
            i += 1
        if i >= len(tex) or tex[i] != "{":
            continue
        units, i = _parse_braced_arg(tex, i + 1)

        while i < len(tex) and tex[i].isspace():
            i += 1
        if i >= len(tex) or tex[i] != "{":
            continue
        equation, i = _parse_braced_arg(tex, i + 1)

        # description + how_to_use are optional in your snippet set (some lines omit args)
        desc = None
        how = None
        for _ in range(2):
            while i < len(tex) and tex[i].isspace():
                i += 1
            if i < len(tex) and tex[i] == "{":
                val, i = _parse_braced_arg(tex, i + 1)
                if desc is None:
                    desc = val
                else:
                    how = val
            else:
                break

        units_norm = units.strip() if units is not None else None
        units_norm = units_norm if units_norm else None

        results.append(
            DeclaredMetric(
                name=name.strip(),
                units=units_norm,
                equation=equation.strip(),
                description=(desc.strip() if isinstance(desc, str) and desc.strip() else None),
                how_to_use=(how.strip() if isinstance(how, str) and how.strip() else None),
            )
        )
    return results

