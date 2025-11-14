"""Qt GUI that exposes the FreeCAD LLM agent inside FreeCAD."""

from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

from .config import AppConfig, load_config
from .pipeline import DesignAgent, PipelineReport
from .llm import fetch_openrouter_models

try:  # pragma: no cover - the GUI is only exercised inside FreeCAD
    from PySide6 import QtCore, QtWidgets  # type: ignore
except ImportError:  # pragma: no cover - fallback for FreeCAD 0.21
    try:
        from PySide2 import QtCore, QtWidgets  # type: ignore
    except ImportError as exc:  # pragma: no cover - PySide is optional
        raise ImportError(
            "PySide2/PySide6 is required to use the GUI integration"
        ) from exc


class AgentDockWidget(QtWidgets.QDockWidget):
    """Dockable widget with request/response areas and OpenRouter settings."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None, config_path: Optional[Path] = None) -> None:
        super().__init__(parent)
        self.setObjectName("LLMAgentDockWidget")
        self.setWindowTitle("LLM агент для FreeCAD")
        self._config_path = config_path
        self._worker_thread: Optional[QtCore.QThread] = None
        self._worker: Optional[_AgentWorker] = None
        self._model_thread: Optional[QtCore.QThread] = None
        self._model_worker: Optional[_ModelFetchWorker] = None

        container = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)

        self._openrouter_group = self._build_openrouter_group()
        layout.addWidget(self._openrouter_group)

        layout.addWidget(QtWidgets.QLabel("Текст задания:"))
        self._requirement_input = QtWidgets.QTextEdit()
        self._requirement_input.setPlaceholderText("Опишите деталь или сборку для генерации...")
        layout.addWidget(self._requirement_input)

        buttons_layout = QtWidgets.QHBoxLayout()
        self._status_label = QtWidgets.QLabel("Готов к запуску")
        self._status_label.setObjectName("agentStatusLabel")
        buttons_layout.addWidget(self._status_label)
        buttons_layout.addStretch()
        self._run_button = QtWidgets.QPushButton("Запустить агента")
        self._run_button.clicked.connect(self._start_agent_run)
        buttons_layout.addWidget(self._run_button)
        clear_button = QtWidgets.QPushButton("Очистить")
        clear_button.clicked.connect(self._clear_texts)
        buttons_layout.addWidget(clear_button)
        layout.addLayout(buttons_layout)

        layout.addWidget(QtWidgets.QLabel("Ответ и отчёт:"))
        self._response_output = QtWidgets.QTextEdit()
        self._response_output.setReadOnly(True)
        layout.addWidget(self._response_output)

        self.setWidget(container)

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------
    def _build_openrouter_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("OpenRouter")
        layout = QtWidgets.QGridLayout(group)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(4)

        self._api_key_edit = QtWidgets.QLineEdit()
        self._api_key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self._api_key_edit.setPlaceholderText("sk-or-v1-...")
        layout.addWidget(QtWidgets.QLabel("API ключ:"), 0, 0)
        layout.addWidget(self._api_key_edit, 0, 1)

        self._base_url_edit = QtWidgets.QLineEdit("https://openrouter.ai/api/v1")
        layout.addWidget(QtWidgets.QLabel("API base:"), 1, 0)
        layout.addWidget(self._base_url_edit, 1, 1)

        self._site_url_edit = QtWidgets.QLineEdit()
        self._site_url_edit.setPlaceholderText("https://example.com")
        layout.addWidget(QtWidgets.QLabel("HTTP-Referer:"), 2, 0)
        layout.addWidget(self._site_url_edit, 2, 1)

        self._app_name_edit = QtWidgets.QLineEdit("FreeCAD LLM Agent")
        layout.addWidget(QtWidgets.QLabel("X-Title:"), 3, 0)
        layout.addWidget(self._app_name_edit, 3, 1)

        self._model_combo = QtWidgets.QComboBox()
        self._model_combo.addItems(
            [
                "gpt-4o-mini",
                "anthropic/claude-3.5-sonnet",
                "meta-llama/llama-3.1-70b-instruct",
            ]
        )
        layout.addWidget(QtWidgets.QLabel("Модель:"), 4, 0)
        layout.addWidget(self._model_combo, 4, 1)

        self._refresh_models_button = QtWidgets.QPushButton("Обновить список моделей")
        self._refresh_models_button.clicked.connect(self._refresh_models)
        layout.addWidget(self._refresh_models_button, 5, 0, 1, 2)

        hint = QtWidgets.QLabel(
            "Заполните ключ и, при необходимости, параметры Referer/X-Title \n"
            "для работы через OpenRouter. Если ключ не указан, используется конфиг агента."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666; font-size: 11px")
        layout.addWidget(hint, 6, 0, 1, 2)
        return group

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------
    def _clear_texts(self) -> None:
        self._requirement_input.clear()
        self._response_output.clear()
        self._set_status("Готов к запуску")

    def _start_agent_run(self) -> None:
        requirement = self._requirement_input.toPlainText().strip()
        if not requirement:
            self._set_status("Введите текст задания", error=True)
            return

        overrides = self._collect_openrouter_overrides()
        self._set_status("Выполняется...", error=False)
        self._run_button.setEnabled(False)
        self._response_output.clear()

        self._worker_thread = QtCore.QThread(self)
        self._worker = _AgentWorker(requirement, self._config_path, overrides)
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._handle_run_success)
        self._worker.failed.connect(self._handle_run_failure)
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker.failed.connect(self._worker_thread.quit)
        self._worker_thread.finished.connect(self._cleanup_worker)
        self._worker_thread.start()

    def _refresh_models(self) -> None:
        api_key = self._api_key_edit.text().strip()
        if not api_key:
            self._set_status("Укажите API ключ OpenRouter", error=True)
            return
        base = self._base_url_edit.text().strip() or None
        self._refresh_models_button.setEnabled(False)
        self._set_status("Загрузка списка моделей...")

        self._model_thread = QtCore.QThread(self)
        self._model_worker = _ModelFetchWorker(api_key, base)
        self._model_worker.moveToThread(self._model_thread)
        self._model_thread.started.connect(self._model_worker.run)
        self._model_worker.finished.connect(self._handle_models_ready)
        self._model_worker.failed.connect(self._handle_models_failed)
        self._model_worker.finished.connect(self._model_thread.quit)
        self._model_worker.failed.connect(self._model_thread.quit)
        self._model_thread.finished.connect(self._cleanup_model_worker)
        self._model_thread.start()

    # ------------------------------------------------------------------
    # Worker callbacks
    # ------------------------------------------------------------------
    def _handle_run_success(self, summary: Dict[str, Any]) -> None:
        self._run_button.setEnabled(True)
        self._set_status("Готово")
        self._response_output.setPlainText(json.dumps(summary, ensure_ascii=False, indent=2))

    def _handle_run_failure(self, message: str) -> None:
        self._run_button.setEnabled(True)
        self._set_status("Ошибка", error=True)
        self._response_output.setPlainText(message)

    def _handle_models_ready(self, models: list[str]) -> None:
        self._refresh_models_button.setEnabled(True)
        self._set_status(f"Получено моделей: {len(models)}")
        self._model_combo.clear()
        if models:
            self._model_combo.addItems(models)
        else:
            self._model_combo.addItem("gpt-4o-mini")

    def _handle_models_failed(self, message: str) -> None:
        self._refresh_models_button.setEnabled(True)
        self._set_status("Не удалось загрузить модели", error=True)
        self._response_output.setPlainText(message)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _collect_openrouter_overrides(self) -> Dict[str, Any]:
        api_key = self._api_key_edit.text().strip()
        model = self._model_combo.currentText().strip()
        if not api_key or not model:
            return {}
        base_url = self._base_url_edit.text().strip() or None
        site_url = self._site_url_edit.text().strip() or None
        app_name = self._app_name_edit.text().strip() or None
        return {
            "provider": "openrouter",
            "api_key": api_key,
            "model": model,
            "openrouter_api_base": base_url,
            "openrouter_site_url": site_url,
            "openrouter_app_name": app_name,
        }

    def _set_status(self, text: str, error: bool = False) -> None:
        self._status_label.setText(text)
        self._status_label.setStyleSheet("color: red" if error else "")

    def _cleanup_worker(self) -> None:
        if self._worker:
            self._worker.deleteLater()
            self._worker = None
        if self._worker_thread:
            self._worker_thread.deleteLater()
            self._worker_thread = None

    def _cleanup_model_worker(self) -> None:
        if self._model_worker:
            self._model_worker.deleteLater()
            self._model_worker = None
        if self._model_thread:
            self._model_thread.deleteLater()
            self._model_thread = None


class _AgentWorker(QtCore.QObject):
    """Executes the DesignAgent in a background thread."""

    finished = QtCore.Signal(dict)
    failed = QtCore.Signal(str)

    def __init__(
        self,
        requirement: str,
        config_path: Optional[Path],
        llm_overrides: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__()
        self._requirement = requirement
        self._config_path = config_path
        self._llm_overrides = llm_overrides or {}

    @QtCore.Slot()
    def run(self) -> None:
        try:
            config = load_config(self._config_path)
            self._apply_llm_overrides(config)
            agent = DesignAgent(config)
            report = agent.run(self._requirement)
            self.finished.emit(_report_to_summary(report))
        except Exception:  # pragma: no cover - defensive
            self.failed.emit(traceback.format_exc())

    def _apply_llm_overrides(self, config: AppConfig) -> None:
        if not self._llm_overrides:
            return
        for key, value in self._llm_overrides.items():
            if value is None:
                continue
            if hasattr(config.llm, key):
                setattr(config.llm, key, value)


class _ModelFetchWorker(QtCore.QObject):
    """Downloads the list of models available via OpenRouter."""

    finished = QtCore.Signal(list)
    failed = QtCore.Signal(str)

    def __init__(self, api_key: str, api_base: Optional[str]) -> None:
        super().__init__()
        self._api_key = api_key
        self._api_base = api_base

    @QtCore.Slot()
    def run(self) -> None:
        try:
            models = fetch_openrouter_models(self._api_key, self._api_base)
            self.finished.emit(models)
        except Exception:  # pragma: no cover - defensive
            self.failed.emit(traceback.format_exc())


def _report_to_summary(report: PipelineReport) -> Dict[str, Any]:
    artifacts = []
    for artifact in report.artifacts:
        artifacts.append(
            {
                "iteration": artifact.iteration,
                "script": str(artifact.script_path),
                "renders": [str(path) for path in artifact.render_paths],
                "success": artifact.success,
                "error": artifact.error,
                "render_feedback": artifact.render_feedback,
                "output_log": artifact.output_log,
            }
        )
    return {"success": report.successful, "artifacts": artifacts, "requirement": report.requirement}


__all__ = ["AgentDockWidget"]
