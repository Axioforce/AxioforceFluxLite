from __future__ import annotations

from typing import Optional, Any

from PySide6 import QtWidgets


class TestingGuideBox(QtWidgets.QGroupBox):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__("Testing Guide", parent)
        layout = QtWidgets.QVBoxLayout(self)

        # Stage progress tracker (Location A/B overview)
        self._tracker = _StageProgressTracker(self)
        layout.addWidget(self._tracker)

        self.guide_label = QtWidgets.QLabel("Use Start Session to begin. Follow prompts here.")
        self.guide_label.setWordWrap(True)
        layout.addWidget(self.guide_label)
        layout.addStretch(1)

    def set_stage_progress(self, stage_text: str, completed_cells: int, total_cells: int) -> None:
        try:
            self.guide_label.setText(
                "Instructions:\n"
                "- Place the specified load in any cell.\n"
                "- Keep COP inside the cell until stable (≈2s, Fz steady).\n"
                "- When captured, the cell will colorize. Move to next cell.\n"
                "- After all cells, follow prompts for the next stage/location."
            )
        except Exception:
            pass

    def set_stage_summary(
        self,
        stages: list[Any] | None,
        *,
        grid_total_cells: int | None = None,
        current_stage_index: int | None = None,
    ) -> None:
        """Update the Location A/B stage tracker from a session's stage list."""
        self._tracker.set_summary(stages or [], grid_total_cells=grid_total_cells, current_stage_index=current_stage_index)


class _StageProgressTracker(QtWidgets.QWidget):
    """Small fixed layout: Location A/B with 3 lines each."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self._rows: dict[tuple[str, str], QtWidgets.QLabel] = {}
        self._name_labels: dict[tuple[str, str], QtWidgets.QLabel] = {}
        self._dots: dict[tuple[str, str], QtWidgets.QLabel] = {}
        self._accent_blue = "#3D7EFF"

        def _section(title: str, loc: str) -> QtWidgets.QWidget:
            w = QtWidgets.QWidget(self)
            lay = QtWidgets.QGridLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setHorizontalSpacing(10)
            lay.setVerticalSpacing(2)

            hdr = QtWidgets.QLabel(title)
            try:
                hdr.setStyleSheet("font-weight: 700; color: #E0E0E0;")
            except Exception:
                pass
            lay.addWidget(hdr, 0, 0, 1, 3)

            for i, st in enumerate(("45 lb", "Two Leg", "One Leg"), start=1):
                dot = QtWidgets.QLabel("●")
                lbl = QtWidgets.QLabel(st)
                val = QtWidgets.QLabel("0/0")
                try:
                    val.setStyleSheet("font-weight: 700;")
                except Exception:
                    pass
                try:
                    dot.setStyleSheet(f"color: {self._accent_blue};")
                    dot.setVisible(False)
                except Exception:
                    pass
                lay.addWidget(dot, i, 0)
                lay.addWidget(lbl, i, 1)
                lay.addWidget(val, i, 2)
                self._rows[(loc, st)] = val
                self._name_labels[(loc, st)] = lbl
                self._dots[(loc, st)] = dot
            lay.setColumnStretch(0, 0)
            lay.setColumnStretch(1, 1)
            lay.setColumnStretch(2, 0)
            return w

        root.addWidget(_section("Location A", "A"))
        root.addWidget(_section("Location B", "B"))

    def set_summary(self, stages: list[Any], *, grid_total_cells: int | None = None, current_stage_index: int | None = None) -> None:
        # Reset first
        for key, lbl in self._rows.items():
            try:
                total = int(grid_total_cells) if grid_total_cells is not None else 0
            except Exception:
                total = 0
            lbl.setText(f"0/{total}" if total else "0/0")
        for k, dot in self._dots.items():
            try:
                dot.setVisible(False)
            except Exception:
                pass
        for k, name_lbl in self._name_labels.items():
            try:
                name_lbl.setStyleSheet("")
            except Exception:
                pass

        def _norm(name: str) -> str | None:
            n = (name or "").strip().lower()
            if not n:
                return None
            if "45" in n:
                return "45 lb"
            if "two" in n:
                return "Two Leg"
            if "one" in n:
                return "One Leg"
            if "body weight" in n and "one" in n:
                return "One Leg"
            if "body weight" in n:
                return "Two Leg"
            return None

        active_key: tuple[str, str] | None = None
        try:
            if current_stage_index is not None and 0 <= int(current_stage_index) < len(stages or []):
                st_active = stages[int(current_stage_index)]
                loc_a = str(getattr(st_active, "location", "") or "").strip().upper() or "A"
                nm_a = _norm(str(getattr(st_active, "name", "") or ""))
                if nm_a is not None:
                    active_key = (loc_a, nm_a)
        except Exception:
            active_key = None

        for st in stages or []:
            try:
                loc = str(getattr(st, "location", "") or "").strip().upper() or "A"
                name = str(getattr(st, "name", "") or "")
                key = _norm(name)
                if key is None:
                    continue
                total = int(getattr(st, "total_cells", 0) or 0) or int(grid_total_cells or 0)
                results = getattr(st, "results", {}) or {}
                done = 0
                for r in results.values():
                    try:
                        if r is not None and getattr(r, "fz_mean_n", None) is not None:
                            done += 1
                    except Exception:
                        continue
                out = self._rows.get((loc, key))
                if out is not None:
                    out.setText(f"{int(done)}/{int(total) if total else 0}")
            except Exception:
                continue

        # Highlight active stage
        if active_key is not None:
            try:
                dot = self._dots.get(active_key)
                if dot is not None:
                    dot.setVisible(True)
            except Exception:
                pass
            try:
                name_lbl = self._name_labels.get(active_key)
                if name_lbl is not None:
                    name_lbl.setStyleSheet(f"color: {self._accent_blue}; font-weight: 700;")
            except Exception:
                pass


