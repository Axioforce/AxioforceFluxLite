from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


try:
    # python-docx
    from docx import Document  # type: ignore
except Exception:  # pragma: no cover
    Document = None  # type: ignore


_METRIC_HEADER_RE = re.compile(r"^\s*(\d+)\.\s+(?P<name>.+?)\s*$")
_OPT_RE = re.compile(r"^\s*optimization\s+(direction|mode)\s*:\s*(?P<val>.+?)\s*$", re.IGNORECASE)


def _norm_heading(s: str) -> str:
    return (s or "").strip().lower().replace("’", "'")


def _is_heading(line: str, heading: str) -> bool:
    return _norm_heading(line) == _norm_heading(heading)


def _collect_until(lines: list[str], start_idx: int, stop_pred) -> tuple[str, int]:
    out: list[str] = []
    i = start_idx
    while i < len(lines):
        if stop_pred(lines[i]):
            break
        if lines[i].strip():
            out.append(lines[i].strip())
        i += 1
    return "\n".join(out).strip(), i


def extract_docx_lines(docx_path: str) -> list[str]:
    if Document is None:
        raise RuntimeError("python-docx is not installed (missing dependency).")
    doc = Document(docx_path)
    lines: list[str] = []

    # Word numbered lists often store numbering in paragraph properties (not in .text).
    # We add a synthetic "N. " prefix for numbered metric headers so downstream parsing works.
    metric_counter = 1
    paras = list(doc.paragraphs)

    def _has_numbering(para) -> bool:
        try:
            return para._p.pPr is not None and para._p.pPr.numPr is not None  # type: ignore[attr-defined]
        except Exception:
            return False

    def _num_level(para) -> int | None:
        """
        List indentation level in Word (0 = top-level). Returns None if not a list paragraph.
        """
        try:
            numPr = para._p.pPr.numPr  # type: ignore[attr-defined]
            if numPr is None or numPr.ilvl is None or numPr.ilvl.val is None:
                return None
            return int(str(numPr.ilvl.val))
        except Exception:
            return None

    def _next_nonempty_text(start_idx: int) -> str | None:
        for j in range(start_idx, len(paras)):
            t = (paras[j].text or "").strip()
            if t:
                return t
        return None

    for i, p in enumerate(paras):
        text = (p.text or "").strip()
        if not text:
            continue

        if _has_numbering(p) and _METRIC_HEADER_RE.match(text) is None:
            nxt = _next_nonempty_text(i + 1) or ""
            lvl = _num_level(p)
            # Common pattern in these docs: header (numbered) -> "How you use it"
            if (
                lvl == 0
                and (
                    _is_heading(nxt, "How you use it")
                    or _is_heading(nxt, "How it's calculated")
                    or _is_heading(nxt, "How it’s calculated")
                    or _is_heading(nxt, "How its calculated")
                    or _is_heading(nxt, "Optimization mode")
                )
            ):
                lines.append(f"{metric_counter}. {text}")
                metric_counter += 1
                continue

        lines.append(text)
    return lines


@dataclass(frozen=True)
class DocMetricBlock:
    name: str
    how_to_use: str | None
    optimization_mode: str | None
    equation_explanation: str | None


def parse_metric_blocks_from_lines(lines: list[str]) -> list[DocMetricBlock]:
    """
    Parse blocks like:
      1. Peak Force
      How you use it
      ...
      Optimization direction: increase
      How it’s calculated
      ...
    """
    blocks: list[tuple[str, list[str]]] = []
    current_name: str | None = None
    current_lines: list[str] = []

    for line in lines:
        m = _METRIC_HEADER_RE.match(line)
        if m:
            if current_name is not None:
                blocks.append((current_name, current_lines))
            current_name = m.group("name").strip()
            current_lines = []
        else:
            if current_name is not None:
                current_lines.append(line)

    if current_name is not None:
        blocks.append((current_name, current_lines))

    # Fallback: if nothing matched "1. Name" style, try a looser heuristic:
    # treat any line followed by "How you use it" as a metric header.
    if not blocks:
        blocks = []
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1
                continue
            nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if _is_heading(nxt, "How you use it"):
                name = line
                body: list[str] = []
                i += 1
                while i < len(lines):
                    m = _METRIC_HEADER_RE.match(lines[i].strip())
                    # stop if we hit another likely header
                    nxt2 = lines[i + 1].strip() if i + 1 < len(lines) else ""
                    if m or _is_heading(nxt2, "How you use it"):
                        break
                    body.append(lines[i])
                    i += 1
                blocks.append((name, body))
            else:
                i += 1

    parsed: list[DocMetricBlock] = []
    for name, body in blocks:
        how_to_use = None
        optimization_mode = None
        equation_explanation = None

        # Walk lines looking for headings
        i = 0
        while i < len(body):
            line = body[i].strip()

            # optimization direction line can appear anywhere
            opt_m = _OPT_RE.match(line)
            if opt_m and optimization_mode is None:
                optimization_mode = opt_m.group("val").strip()
                i += 1
                continue

            if _is_heading(line, "How you use it") and how_to_use is None:
                # New expected format: next bullet contains content
                next_line = body[i + 1].strip() if i + 1 < len(body) else ""
                if next_line and not _is_heading(next_line, "How it's calculated") and not _is_heading(
                    next_line, "How it’s calculated"
                ):
                    how_to_use = next_line
                    i += 2
                    continue

            if (
                _is_heading(line, "How it's calculated")
                or _is_heading(line, "How it’s calculated")
                or _is_heading(line, "How its calculated")
            ):
                next_line = body[i + 1].strip() if i + 1 < len(body) else ""
                if next_line and not _is_heading(next_line, "How you use it") and not _is_heading(
                    next_line, "Optimization mode"
                ):
                    equation_explanation = next_line
                    i += 2
                    continue

            if _is_heading(line, "Optimization mode") and optimization_mode is None:
                next_line = body[i + 1].strip() if i + 1 < len(body) else ""
                if next_line:
                    optimization_mode = next_line
                    i += 2
                    continue

            # Some docs use "Optimization direction" as a heading with the value on the next line.
            if _is_heading(line, "Optimization direction") and optimization_mode is None:
                next_line = body[i + 1].strip() if i + 1 < len(body) else ""
                if next_line:
                    optimization_mode = next_line
                    i += 2
                    continue

            i += 1

        parsed.append(
            DocMetricBlock(
                name=name,
                how_to_use=how_to_use,
                optimization_mode=optimization_mode,
                equation_explanation=equation_explanation,
            )
        )

    return parsed

