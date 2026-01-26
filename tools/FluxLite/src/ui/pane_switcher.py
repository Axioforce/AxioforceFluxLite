from __future__ import annotations
from typing import Dict, Optional, Any
from PySide6 import QtWidgets

class PaneSwitcher:
    """
    Helper to manage automatic switching of QTabWidget tabs or QStackedWidgets
    based on application state or user actions.
    
    Usage:
        switcher = PaneSwitcher()
        switcher.register_tab(tab_widget, widget_instance, "view_name")
        switcher.switch_to("view_name")
    """
    
    def __init__(self) -> None:
        # Maps view_name -> (parent_tab_widget, actual_widget)
        self._registry: Dict[str, tuple[QtWidgets.QTabWidget | QtWidgets.QStackedWidget, QtWidgets.QWidget]] = {}

    def register_tab(self, 
                     container: QtWidgets.QTabWidget | QtWidgets.QStackedWidget, 
                     widget: QtWidgets.QWidget, 
                     view_name: str) -> None:
        """Register a widget and its container under a semantic name."""
        if not container or not widget:
            return
        self._registry[view_name] = (container, widget)

    def switch_to(self, view_name: str) -> bool:
        """
        Switch the container to show the registered widget for view_name.
        Returns True if successful, False if view_name not found.
        """
        if view_name not in self._registry:
            return False
            
        container, widget = self._registry[view_name]
        
        try:
            if isinstance(container, QtWidgets.QTabWidget):
                # Find index of this widget in the tab widget
                idx = container.indexOf(widget)
                if idx >= 0:
                    container.setCurrentIndex(idx)
                    return True
            elif isinstance(container, QtWidgets.QStackedWidget):
                container.setCurrentWidget(widget)
                return True
        except Exception:
            pass
            
        return False

    def switch_many(self, *view_names: str) -> None:
        """Switch multiple panes at once (e.g. left and right tabs)."""
        for name in view_names:
            self.switch_to(name)


