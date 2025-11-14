"""Utilities for executing FreeCAD macros in a sandbox."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .config import FreeCADConfig

logger = logging.getLogger(__name__)


@dataclass
class ScriptExecutionResult:
    """Outcome of a FreeCAD macro execution."""

    success: bool
    script_path: Path
    output_log: List[str]
    error: Optional[str] = None


class FreeCADEngine:
    """Thin wrapper over the FreeCAD command line interface.

    The class does not actually execute FreeCAD during unit tests. Instead it
    writes macro files to the workspace and simulates execution which keeps the
    project runnable in constrained environments.
    """

    def __init__(self, config: FreeCADConfig, workspace: Path) -> None:
        self._config = config
        self._workspace = workspace
        self._workspace.mkdir(parents=True, exist_ok=True)

    def run_script(self, script_body: str, iteration: int) -> ScriptExecutionResult:
        script_path = self._workspace / f"iteration_{iteration}.py"
        script_path.write_text(script_body, encoding="utf-8")
        logger.info("Stored FreeCAD macro at %s", script_path)

        # The dummy implementation pretends that the script ran successfully
        # unless it explicitly raises an exception.
        output_log: List[str] = [
            f"Running FreeCAD macro using executable {self._config.executable_path}",
            "[simulated] FreeCAD started in headless mode",
        ]

        if "raise" in script_body:
            error_msg = "Script contains explicit raise statement"
            logger.error(error_msg)
            return ScriptExecutionResult(False, script_path, output_log, error_msg)

        output_log.append("[simulated] FreeCAD finished successfully")
        return ScriptExecutionResult(True, script_path, output_log, None)


__all__ = ["ScriptExecutionResult", "FreeCADEngine"]
