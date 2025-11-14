"""Helpers for integrating the agent into the FreeCAD GUI."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

try:  # pragma: no cover - executed only inside FreeCAD
    import FreeCADGui  # type: ignore
except ImportError:  # pragma: no cover - allows importing the module outside FreeCAD
    FreeCADGui = None  # type: ignore

try:  # pragma: no cover - GUI-only dependency
    from PySide6 import QtCore, QtWidgets  # type: ignore
except ImportError:  # pragma: no cover
    from PySide2 import QtCore, QtWidgets  # type: ignore

from .gui import AgentDockWidget


def show_agent_dock_widget(config_path: Optional[Path] = None) -> AgentDockWidget:
    """Create (or replace) the dock widget inside the FreeCAD main window."""

    widget = AgentDockWidget(config_path=config_path)
    if FreeCADGui:
        main_window = FreeCADGui.getMainWindow()
        # Remove previous instance if it exists to avoid duplicates.
        previous = main_window.findChild(QtWidgets.QDockWidget, widget.objectName())
        if previous:
            main_window.removeDockWidget(previous)
            previous.setParent(None)
            previous.deleteLater()
        main_window.addDockWidget(QtCore.Qt.RightDockWidgetArea, widget)
    widget.show()
    widget.raise_()
    return widget


__all__ = ["show_agent_dock_widget"]
