"""Utilities for executing FreeCAD macros in a sandbox."""

from __future__ import annotations

import contextlib
import io
import logging
import os
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .config import FreeCADConfig

logger = logging.getLogger(__name__)

try:  # pragma: no cover - optional dependency
    import FreeCAD  # type: ignore
except ImportError:  # pragma: no cover - FreeCAD not available
    FreeCAD = None  # type: ignore

try:  # pragma: no cover - optional dependency
    import FreeCADGui  # type: ignore
except ImportError:  # pragma: no cover - GUI might be unavailable
    FreeCADGui = None  # type: ignore


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
        self._embedded_runner = _EmbeddedFreeCADRuntime.try_create()
        self._executable = self._discover_executable()

    def run_script(self, script_body: str, iteration: int) -> ScriptExecutionResult:
        script_path = self._workspace / f"iteration_{iteration}.py"
        script_path.write_text(script_body, encoding="utf-8")
        logger.info("Stored FreeCAD macro at %s", script_path)

        if self._embedded_runner:
            return self._embedded_runner.execute(script_body, script_path)

        if self._executable:
            return self._run_with_freecad(script_path)

        return self._simulate_execution(script_body, script_path)

    def _discover_executable(self) -> Optional[str]:
        path = self._config.executable_path
        if path.exists():
            return str(path)
        found = shutil.which(str(path))
        if found:
            return found
        logger.warning("FreeCAD executable %s not found. Falling back to simulation.", path)
        return None

    def _run_with_freecad(self, script_path: Path) -> ScriptExecutionResult:
        log_path = script_path.with_suffix(".log")
        cmd = [self._executable, "-l", str(log_path), str(script_path)]  # type: ignore[list-item]
        env = os.environ.copy()
        if self._config.headless:
            env.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._config.timeout_seconds,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return ScriptExecutionResult(
                success=False,
                script_path=script_path,
                output_log=["FreeCAD execution timed out"],
                error="Execution timed out",
            )
        except FileNotFoundError:
            logger.error("FreeCAD executable disappeared at runtime. Using simulation.")
            self._executable = None
            return self._simulate_execution(script_path.read_text(encoding="utf-8"), script_path)

        output_log = _split_lines(completed.stdout) + _split_lines(completed.stderr)
        if log_path.exists():
            output_log.extend(log_path.read_text(encoding="utf-8").splitlines())
        error = None
        if completed.returncode != 0:
            error = f"FreeCAD exited with code {completed.returncode}"
        else:
            error = self._scan_for_errors(output_log)
        return ScriptExecutionResult(error is None, script_path, output_log, error)

    def _simulate_execution(self, script_body: str, script_path: Path) -> ScriptExecutionResult:
        output_log: List[str] = [
            f"[simulated] Running FreeCAD macro {script_path.name}",
            "[simulated] FreeCAD started in headless mode",
        ]
        if "raise" in script_body:
            error_msg = "Script contains explicit raise statement"
            logger.error(error_msg)
            return ScriptExecutionResult(False, script_path, output_log, error_msg)
        output_log.append("[simulated] FreeCAD finished successfully")
        return ScriptExecutionResult(True, script_path, output_log, None)

    def _scan_for_errors(self, log_lines: List[str]) -> Optional[str]:
        joined = "\n".join(log_lines)
        if "Traceback" in joined:
            return "FreeCAD reported a traceback"
        for marker in ["[ERR]", "Error:", "RuntimeError", "Exception"]:
            if marker in joined:
                return f"Detected error marker '{marker}' in FreeCAD log"
        return None


def _split_lines(data: Optional[str]) -> List[str]:
    if not data:
        return []
    return data.splitlines()


class _EmbeddedFreeCADRuntime:
    """Executes macros inside the currently running FreeCAD session."""

    def __init__(self) -> None:
        self._namespace: Dict[str, object] = {}

    @classmethod
    def try_create(cls) -> Optional["_EmbeddedFreeCADRuntime"]:
        if FreeCAD is None:
            return None
        return cls()

    def execute(self, script_body: str, script_path: Path) -> ScriptExecutionResult:
        buffer = io.StringIO()
        try:
            compiled = compile(script_body, str(script_path), "exec")
            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                exec(compiled, self._namespace)
            output_log = _split_lines(buffer.getvalue())
            self._refresh_gui_view()
            return ScriptExecutionResult(True, script_path, output_log)
        except Exception as exc:  # pragma: no cover - depends on FreeCAD runtime
            output_log = _split_lines(buffer.getvalue())
            output_log.extend(traceback.format_exc().splitlines())
            return ScriptExecutionResult(False, script_path, output_log, str(exc))

    def _refresh_gui_view(self) -> None:
        if FreeCAD is not None:
            document = getattr(FreeCAD, "ActiveDocument", None)
            if document:
                try:
                    document.recompute()
                except Exception:  # pragma: no cover - FreeCAD specific
                    logger.debug("Failed to recompute active document", exc_info=True)

        if FreeCADGui is None:
            return

        try:
            gui_document = getattr(FreeCADGui, "ActiveDocument", None)
            view = getattr(gui_document, "ActiveView", None) if gui_document else None
            if view:
                try:
                    fit_all = getattr(view, "fitAll", None)
                    if callable(fit_all):
                        fit_all()
                    update_view = getattr(view, "update", None)
                    if callable(update_view):
                        update_view()
                except Exception:  # pragma: no cover - FreeCAD specific
                    logger.debug("Failed to refresh FreeCAD view", exc_info=True)

            update_gui = getattr(FreeCADGui, "updateGui", None)
            if callable(update_gui):
                update_gui()
            send_msg = getattr(FreeCADGui, "SendMsgToActiveView", None)
            if callable(send_msg):
                send_msg("ViewFit")
        except Exception:  # pragma: no cover - FreeCAD specific
            logger.debug("Failed to update FreeCAD GUI", exc_info=True)


__all__ = ["ScriptExecutionResult", "FreeCADEngine"]
