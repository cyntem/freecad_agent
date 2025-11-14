"""Initialization entry point for the FreeCAD LLM agent module."""

try:  # pragma: no cover - executed only inside FreeCAD
    import FreeCAD  # type: ignore
except ImportError:  # pragma: no cover
    FreeCAD = None  # type: ignore


def Initialize() -> None:  # pragma: no cover - FreeCAD callback
    if FreeCAD:
        FreeCAD.Console.PrintMessage("FreeCAD LLM agent module initialized\n")


def FreeCADStart() -> None:  # pragma: no cover - FreeCAD callback alias
    Initialize()
