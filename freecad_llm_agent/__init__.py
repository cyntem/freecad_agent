"""Top level package for the FreeCAD LLM agent."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("freecad_llm_agent")
except PackageNotFoundError:  # pragma: no cover - the package is not installed in editable mode
    __version__ = "0.1.0"

__all__ = ["__version__"]
