from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from PySide6 import QtCore, QtGui, QtWidgets

from .tool_registry import ToolSpec


class ToolLauncherPage(QtWidgets.QWidget):
    tool_selected = QtCore.Signal(str)  # tool_id

    def __init__(self, tools: Iterable[ToolSpec], parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._tools = list(tools)
        self._setup_ui()

    def _setup_ui(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(12)

        # Optional logo. Render to a pixmap to preserve aspect ratio and
        # avoid any widget sizing "stretch" behavior.
        logo_path = self._resolve_logo_path()
        if logo_path is not None:
            logo = self._build_logo_widget(logo_path)
            if logo is not None:
                outer.addWidget(logo)

        self._grid = QtWidgets.QGridLayout()
        self._grid.setHorizontalSpacing(10)
        self._grid.setVerticalSpacing(10)

        grid_wrap = QtWidgets.QWidget()
        grid_wrap.setLayout(self._grid)
        outer.addWidget(grid_wrap, 1)

        # Simple "chip grid" made of flat toolbuttons.
        cols = 3
        for i, tool in enumerate(self._tools):
            r, c = divmod(i, cols)
            btn = QtWidgets.QToolButton()
            btn.setText(tool.name)
            btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
            btn.setAutoRaise(False)
            btn.setCheckable(False)
            # Square tool tiles
            btn.setMinimumSize(150, 150)
            btn.setMaximumSize(150, 150)
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, tid=tool.tool_id: self.tool_selected.emit(tid))
            # Bigger text + white tile background
            f = btn.font()
            f.setPointSize(12)
            f.setBold(True)
            btn.setFont(f)
            btn.setStyleSheet(
                "QToolButton {"
                "  background: #1A1A1A;"
                "  color: #E0E0E0;"
                "  border: 1px solid rgba(224, 224, 224, 35);"
                "  border-radius: 12px;"
                "  padding: 10px;"
                "}"
                "QToolButton:hover {"
                "  border: 1px solid rgba(224, 224, 224, 70);"
                "}"
                "QToolButton:pressed {"
                "  background: #202020;"
                "}"
            )

            # Use tooltip for extra info, keep the grid clean.
            tip = tool.description.strip()
            if tool.kind == "web" and tool.url:
                tip = (tip + "\n\n" if tip else "") + tool.url
            if tip:
                btn.setToolTip(tip)

            self._grid.addWidget(btn, r, c)

    def _build_logo_widget(self, path: Path) -> QtWidgets.QWidget | None:
        """
        Create a top-left logo widget that preserves aspect ratio and looks crisp.
        """
        # Target display size (~50% smaller)
        max_w = 210
        max_h = 55

        pm = self._render_logo_pixmap(path, max_w=max_w, max_h=max_h)
        if pm is None or pm.isNull():
            return None

        lbl = QtWidgets.QLabel()
        lbl.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        lbl.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        lbl.setPixmap(pm)

        wrap = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(lbl, 0, QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        row.addStretch(1)
        return wrap

    def _render_logo_pixmap(self, path: Path, *, max_w: int, max_h: int) -> QtGui.QPixmap | None:
        """
        Render SVG (or load raster) into a high-DPI pixmap, scaled to fit within max_w/max_h
        while preserving aspect ratio.
        """
        suffix = path.suffix.lower()
        dpr = 1.0
        try:
            dpr = float(self.devicePixelRatioF())
        except Exception:
            dpr = 1.0

        if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            pm = QtGui.QPixmap(str(path))
            if pm.isNull():
                return None
            # Scale in device pixels for crispness, then tag DPR for correct logical sizing.
            scaled = pm.scaled(
                int(max_w * dpr),
                int(max_h * dpr),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(dpr)
            return scaled

        if suffix == ".svg":
            try:
                from PySide6 import QtSvg  # type: ignore
            except Exception:
                return None

            renderer = QtSvg.QSvgRenderer(str(path))
            if not renderer.isValid():
                return None

            default = renderer.defaultSize()
            w0 = int(default.width()) if default.width() > 0 else max_w
            h0 = int(default.height()) if default.height() > 0 else max_h
            if w0 <= 0 or h0 <= 0:
                w0, h0 = max_w, max_h

            # Fit into max_w/max_h preserving aspect ratio.
            scale = min(max_w / float(w0), max_h / float(h0), 1.0)
            w = max(1, int(w0 * scale))
            h = max(1, int(h0 * scale))

            # Render at a higher pixel density than the screen DPR to avoid a "raster-y" look.
            # QtSvg ultimately paints vectors, but the widget display is raster-backed.
            render_dpr = max(dpr, 3.0)

            img = QtGui.QImage(
                int(w * render_dpr),
                int(h * render_dpr),
                QtGui.QImage.Format_ARGB32_Premultiplied,
            )
            img.fill(QtCore.Qt.transparent)

            p = QtGui.QPainter(img)
            try:
                p.setRenderHint(QtGui.QPainter.Antialiasing, True)
                p.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
                p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
                renderer.render(p, QtCore.QRectF(0.0, 0.0, float(img.width()), float(img.height())))
            finally:
                p.end()

            pm = QtGui.QPixmap.fromImage(img)
            pm.setDevicePixelRatio(render_dpr)
            return pm

        return None

    def _resolve_logo_path(self) -> Path | None:
        # 1) Repo-local convention (preferred when you later commit the logo)
        ui_dir = Path(__file__).resolve().parents[1]
        candidate_logos = ui_dir / "assets" / "logos" / "FluxDeluxeLogo.png"
        if candidate_logos.exists():
            return candidate_logos
        candidate_logos = ui_dir / "assets" / "logos" / "FluxDeluxeLogo.svg"
        if candidate_logos.exists():
            return candidate_logos
        candidate_logo = ui_dir / "assets" / "logo" / "FluxDeluxeLogo.svg"
        if candidate_logo.exists():
            return candidate_logo
        candidate_icons = ui_dir / "assets" / "icons" / "FluxDeluxeLogo.svg"
        if candidate_icons.exists():
            return candidate_icons

        # 2) Optional env override
        env_path = str(os.environ.get("FLUXDELUXE_LOGO_SVG", "") or "").strip()
        if env_path:
            p = Path(env_path)
            if p.exists():
                return p

        return None

