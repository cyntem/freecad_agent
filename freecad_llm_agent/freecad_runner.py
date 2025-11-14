"""Utilities for executing FreeCAD macros in a sandbox."""

from __future__ import annotations

import contextlib
import io
import logging
import os
import shutil
import subprocess
import traceback
import threading
from dataclasses import dataclass, field
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

try:  # pragma: no cover - optional dependency
    from PySide6 import QtCore  # type: ignore
except ImportError:  # pragma: no cover - fallback for FreeCAD 0.21
    try:
        from PySide2 import QtCore  # type: ignore
    except ImportError:  # pragma: no cover - Qt bridge unavailable
        QtCore = None  # type: ignore


@dataclass
class ScriptExecutionResult:
    """Outcome of a FreeCAD macro execution."""

    success: bool
    script_path: Path
    output_log: List[str]
    error: Optional[str] = None
    affected_objects: List[str] = field(default_factory=list)


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
                affected_objects=[],
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
        return ScriptExecutionResult(error is None, script_path, output_log, error, [])

    def _simulate_execution(self, script_body: str, script_path: Path) -> ScriptExecutionResult:
        output_log: List[str] = [
            f"[simulated] Running FreeCAD macro {script_path.name}",
            "[simulated] FreeCAD started in headless mode",
        ]
        if "raise" in script_body:
            error_msg = "Script contains explicit raise statement"
            logger.error(error_msg)
            return ScriptExecutionResult(False, script_path, output_log, error_msg, [])
        output_log.append("[simulated] FreeCAD finished successfully")
        return ScriptExecutionResult(True, script_path, output_log, None, [])

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
        self._project_doc_name = "LLMAgentProject"
        self._qt_executor: Optional["_QtMainThreadExecutor"] = None
        if QtCore is not None:
            try:
                self._qt_executor = _QtMainThreadExecutor(self)
            except Exception:  # pragma: no cover - depends on FreeCAD runtime
                logger.debug("Failed to initialize Qt main-thread executor", exc_info=True)

    @classmethod
    def try_create(cls) -> Optional["_EmbeddedFreeCADRuntime"]:
        if FreeCAD is None:
            return None
        if threading.current_thread() is threading.main_thread():
            return cls()
        if _QtMainThreadExecutor.is_available():
            return cls()
        logger.debug(
            "Embedded FreeCAD runtime requires the main thread or a Qt event loop bridge."
        )
        return None

    def execute(self, script_body: str, script_path: Path) -> ScriptExecutionResult:
        if threading.current_thread() is threading.main_thread():
            return self._execute_internal(script_body, script_path)
        if self._qt_executor:
            return self._qt_executor.execute(script_body, script_path)
        logger.debug("Falling back to direct execution outside the main thread")
        return self._execute_internal(script_body, script_path)

    def _execute_internal(self, script_body: str, script_path: Path) -> ScriptExecutionResult:
        buffer = io.StringIO()
        before_objects = self._capture_document_objects()
        try:
            self._ensure_project_document()
            compiled = compile(script_body, str(script_path), "exec")
            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                exec(compiled, self._namespace)
            output_log = _split_lines(buffer.getvalue())
            self._refresh_gui_view()
            affected = self._calculate_affected_objects(before_objects)
            return ScriptExecutionResult(True, script_path, output_log, None, affected)
        except Exception as exc:  # pragma: no cover - depends on FreeCAD runtime
            output_log = _split_lines(buffer.getvalue())
            output_log.extend(traceback.format_exc().splitlines())
            affected = self._calculate_affected_objects(before_objects)
            return ScriptExecutionResult(False, script_path, output_log, str(exc), affected)

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

    def _ensure_project_document(self) -> None:
        if FreeCAD is None:
            return
        document = getattr(FreeCAD, "ActiveDocument", None)
        if document is None:
            document = self._document_from_gui()
        if document is None:
            document = self._get_existing_document()
        if document is None:
            create_document = getattr(FreeCAD, "newDocument", None)
            if callable(create_document):
                try:
                    document = create_document(self._project_doc_name)
                except Exception:  # pragma: no cover - FreeCAD specific
                    logger.debug("Failed to create persistent FreeCAD document", exc_info=True)
                    document = None
        else:
            try:
                self._project_doc_name = document.Name
            except Exception:  # pragma: no cover - FreeCAD specific
                logger.debug("Failed to read active document name", exc_info=True)
        if document is None:
            return
        set_active = getattr(FreeCAD, "setActiveDocument", None)
        if callable(set_active):
            try:
                set_active(document.Name)
            except Exception:  # pragma: no cover - FreeCAD specific
                logger.debug("Failed to set active document", exc_info=True)
        FreeCAD.ActiveDocument = document
        self._activate_gui_document(document)

    def _document_from_gui(self):  # type: ignore[no-untyped-def]
        if FreeCADGui is None:
            return None
        gui_document = getattr(FreeCADGui, "ActiveDocument", None)
        if gui_document is None:
            return None
        document = getattr(gui_document, "Document", None)
        return document

    def _get_existing_document(self):  # type: ignore[no-untyped-def]
        get_doc = getattr(FreeCAD, "getDocument", None)
        if callable(get_doc):
            try:
                return get_doc(self._project_doc_name)
            except Exception:  # pragma: no cover - FreeCAD specific
                logger.debug("Failed to fetch existing FreeCAD document", exc_info=True)
        return None

    def _activate_gui_document(self, document) -> None:  # type: ignore[no-untyped-def]
        if FreeCADGui is None:
            return
        get_doc = getattr(FreeCADGui, "getDocument", None)
        gui_document = None
        if callable(get_doc):
            try:
                gui_document = get_doc(document.Name)
            except Exception:  # pragma: no cover - FreeCAD specific
                logger.debug("Failed to get GUI document", exc_info=True)
        if gui_document is None:
            gui_document = getattr(FreeCADGui, "ActiveDocument", None)
        if gui_document is not None:
            FreeCADGui.ActiveDocument = gui_document

    def _capture_document_objects(self) -> Dict[str, str]:
        objects: Dict[str, str] = {}
        if FreeCAD is None:
            return objects
        document = getattr(FreeCAD, "ActiveDocument", None)
        if document is None:
            return objects
        doc_objects = getattr(document, "Objects", None)
        if doc_objects:
            for obj in doc_objects:
                name = getattr(obj, "Name", None)
                label = getattr(obj, "Label", None)
                if name:
                    objects[name] = label or name
        active_obj = getattr(document, "ActiveObject", None)
        if active_obj is not None:
            name = getattr(active_obj, "Name", None)
            label = getattr(active_obj, "Label", None)
            if name:
                objects.setdefault(name, label or name)
        return objects

    def _calculate_affected_objects(self, before: Dict[str, str]) -> List[str]:
        after = self._capture_document_objects()
        new_objects = [label for name, label in after.items() if name not in before]
        if new_objects:
            return new_objects
        # Fall back to the current active object if nothing new was created
        if FreeCAD is not None:
            document = getattr(FreeCAD, "ActiveDocument", None)
            if document is not None:
                active_obj = getattr(document, "ActiveObject", None)
                if active_obj is not None:
                    name = getattr(active_obj, "Name", None)
                    label = getattr(active_obj, "Label", None)
                    if name or label:
                        return [label or name]
        return []


if QtCore is not None:

    class _QtMainThreadExecutor(QtCore.QObject):  # type: ignore[misc]
        """Runs embedded FreeCAD scripts on the GUI thread via Qt signals."""

        _finished = QtCore.Signal(object)
        _execute_requested = QtCore.Signal(str, str)

        def __init__(self, runtime: _EmbeddedFreeCADRuntime) -> None:
            super().__init__()
            self._runtime = runtime
            app = QtCore.QCoreApplication.instance()
            if app is None:
                raise RuntimeError("Qt application instance is required for main-thread execution")
            self.moveToThread(app.thread())
            self._execute_requested.connect(self._run_on_main_thread)

        @staticmethod
        def is_available() -> bool:
            if QtCore is None:
                return False
            return QtCore.QCoreApplication.instance() is not None

        @QtCore.Slot(str, str)
        def _run_on_main_thread(self, script_body: str, script_path_str: str) -> None:
            script_path = Path(script_path_str)
            result = self._runtime._execute_internal(script_body, script_path)
            self._finished.emit(result)

        def execute(self, script_body: str, script_path: Path) -> ScriptExecutionResult:
            loop = QtCore.QEventLoop()
            result_container: List[ScriptExecutionResult] = []

            def _handle(result: ScriptExecutionResult) -> None:
                result_container.append(result)
                loop.quit()

            self._finished.connect(_handle)
            try:
                self._execute_requested.emit(script_body, str(script_path))
                exec_method = getattr(loop, "exec", None) or getattr(loop, "exec_", None)
                if exec_method is None:  # pragma: no cover - Qt specific
                    raise RuntimeError("Qt event loop does not provide an exec method")
                exec_method()
            finally:
                self._finished.disconnect(_handle)

            if not result_container:
                raise RuntimeError("Qt bridge failed to return script execution result")
            return result_container[0]

else:

    class _QtMainThreadExecutor:  # type: ignore[too-few-public-methods]
        """Placeholder used when PySide is unavailable."""

        @staticmethod
        def is_available() -> bool:
            return False

__all__ = ["ScriptExecutionResult", "FreeCADEngine"]
