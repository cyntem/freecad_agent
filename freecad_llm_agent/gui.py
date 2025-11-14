"""Qt GUI that exposes the FreeCAD LLM agent inside FreeCAD."""

from __future__ import annotations

import json
import threading
import traceback
from pathlib import Path
from typing import Any, Dict, Optional, List

from .config import AppConfig, load_config
from .pipeline import DesignAgent, PipelineReport, PipelineCancelledError
from .llm import OpenRouterModelInfo, fetch_openrouter_models
from .freecad_runner import FreeCADEngine

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
        self._settings = QtCore.QSettings("FreeCAD", "LLMAgent")
        self._worker_thread: Optional[QtCore.QThread] = None
        self._worker: Optional[_AgentWorker] = None
        self._cancel_event: Optional[threading.Event] = None
        self._model_thread: Optional[QtCore.QThread] = None
        self._model_worker: Optional[_ModelFetchWorker] = None
        self._models_by_vendor: Dict[str, List[OpenRouterModelInfo]] = {}
        self._pending_vendor_key: Optional[str] = None
        self._pending_model_id: Optional[str] = None
        self._suspend_selection_persistence = True
        self._artifact_data: List[Dict[str, Any]] = []
        self._success_icon = self.style().standardIcon(QtWidgets.QStyle.SP_DialogApplyButton)
        self._failure_icon = self.style().standardIcon(QtWidgets.QStyle.SP_MessageBoxCritical)
        self._macro_thread: Optional[QtCore.QThread] = None
        self._macro_worker: Optional["_MacroExecutionWorker"] = None

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

        iteration_layout = QtWidgets.QHBoxLayout()
        iteration_layout.addWidget(QtWidgets.QLabel("Максимум итераций:"))
        self._iteration_spin = QtWidgets.QSpinBox()
        self._iteration_spin.setRange(1, 10)
        self._iteration_spin.valueChanged.connect(self._persist_iteration_count)
        iteration_layout.addWidget(self._iteration_spin)
        iteration_layout.addStretch()
        layout.addLayout(iteration_layout)

        buttons_layout = QtWidgets.QHBoxLayout()
        self._status_label = QtWidgets.QLabel("Готов к запуску")
        self._status_label.setObjectName("agentStatusLabel")
        buttons_layout.addWidget(self._status_label)
        buttons_layout.addStretch()
        self._run_button = QtWidgets.QPushButton("Запустить агента")
        self._run_button.clicked.connect(self._start_agent_run)
        buttons_layout.addWidget(self._run_button)
        self._cancel_button = QtWidgets.QPushButton("Отменить запрос")
        self._cancel_button.setEnabled(False)
        self._cancel_button.clicked.connect(self._cancel_agent_run)
        buttons_layout.addWidget(self._cancel_button)
        clear_button = QtWidgets.QPushButton("Очистить")
        clear_button.clicked.connect(self._clear_texts)
        buttons_layout.addWidget(clear_button)
        layout.addLayout(buttons_layout)

        layout.addWidget(QtWidgets.QLabel("Ответ и отчёт:"))
        self._response_output = QtWidgets.QTextEdit()
        self._response_output.setReadOnly(True)
        layout.addWidget(self._response_output)

        layout.addWidget(QtWidgets.QLabel("Сгенерированные макросы:"))
        scripts_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self._artifact_list = QtWidgets.QListWidget()
        self._artifact_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._artifact_list.itemSelectionChanged.connect(self._display_selected_artifact)
        scripts_splitter.addWidget(self._artifact_list)
        self._script_preview = QtWidgets.QPlainTextEdit()
        self._script_preview.setReadOnly(True)
        self._script_preview.setPlaceholderText("Выберите макрос для просмотра кода")
        scripts_splitter.addWidget(self._script_preview)
        scripts_splitter.setStretchFactor(0, 1)
        scripts_splitter.setStretchFactor(1, 2)
        layout.addWidget(scripts_splitter)

        manual_run_layout = QtWidgets.QHBoxLayout()
        manual_run_layout.addStretch()
        self._run_selected_button = QtWidgets.QPushButton("Выполнить выбранный макрос")
        self._run_selected_button.setEnabled(False)
        self._run_selected_button.clicked.connect(self._execute_selected_macro)
        manual_run_layout.addWidget(self._run_selected_button)
        layout.addLayout(manual_run_layout)

        self.setWidget(container)
        self._load_persistent_settings()
        self._seed_default_models()
        self._load_iteration_default()
        self._suspend_selection_persistence = False
        self._attempt_initial_model_refresh()

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
        self._api_key_edit.editingFinished.connect(self._persist_api_key)
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

        self._vendor_combo = QtWidgets.QComboBox()
        self._vendor_combo.currentIndexChanged.connect(self._on_vendor_changed)
        layout.addWidget(QtWidgets.QLabel("Производитель:"), 4, 0)
        layout.addWidget(self._vendor_combo, 4, 1)

        self._model_combo = QtWidgets.QComboBox()
        self._model_combo.currentIndexChanged.connect(self._on_model_changed)
        layout.addWidget(QtWidgets.QLabel("Модель:"), 5, 0)
        layout.addWidget(self._model_combo, 5, 1)

        self._model_capabilities_label = QtWidgets.QLabel("Поддержка изображений: неизвестно")
        self._model_capabilities_label.setObjectName("modelCapabilityHint")
        layout.addWidget(self._model_capabilities_label, 6, 0, 1, 2)

        self._refresh_models_button = QtWidgets.QPushButton("Обновить список моделей")
        self._refresh_models_button.clicked.connect(self._refresh_models)
        layout.addWidget(self._refresh_models_button, 7, 0, 1, 2)

        hint = QtWidgets.QLabel(
            "Заполните ключ и, при необходимости, параметры Referer/X-Title \n"
            "для работы через OpenRouter. Если ключ не указан, используется конфиг агента."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666; font-size: 11px")
        layout.addWidget(hint, 8, 0, 1, 2)
        return group

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------
    def _clear_texts(self) -> None:
        self._requirement_input.clear()
        self._response_output.clear()
        self._set_status("Готов к запуску")
        self._clear_artifacts()

    def _start_agent_run(self) -> None:
        requirement = self._requirement_input.toPlainText().strip()
        if not requirement:
            self._set_status("Введите текст задания", error=True)
            return

        overrides = self._collect_openrouter_overrides()
        self._set_status("Выполняется...", error=False)
        self._run_button.setEnabled(False)
        self._cancel_button.setEnabled(True)
        self._response_output.clear()
        self._clear_artifacts()
        self._persist_iteration_count()

        self._cancel_event = threading.Event()
        self._worker_thread = QtCore.QThread(self)
        self._worker = _AgentWorker(
            requirement,
            self._config_path,
            overrides,
            self._iteration_spin.value(),
            self._cancel_event,
        )
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._handle_run_success)
        self._worker.failed.connect(self._handle_run_failure)
        self._worker.cancelled.connect(self._handle_run_cancelled)
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker.failed.connect(self._worker_thread.quit)
        self._worker.cancelled.connect(self._worker_thread.quit)
        self._worker_thread.finished.connect(self._cleanup_worker)
        self._worker_thread.start()

    def _cancel_agent_run(self) -> None:
        if self._cancel_event and not self._cancel_event.is_set():
            self._cancel_event.set()
            self._cancel_button.setEnabled(False)
            self._set_status("Отмена запроса...")

    def _refresh_models(self) -> None:
        api_key = self._api_key_edit.text().strip()
        if not api_key:
            self._set_status("Укажите API ключ OpenRouter", error=True)
            return
        self._persist_api_key()
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
        self._finalize_agent_run()
        self._set_status("Готово")
        self._response_output.setPlainText(json.dumps(summary, ensure_ascii=False, indent=2))
        self._update_artifact_list(summary)

    def _handle_run_failure(self, message: str) -> None:
        self._finalize_agent_run()
        self._set_status("Ошибка", error=True)
        self._response_output.setPlainText(message)
        self._clear_artifacts()

    def _handle_run_cancelled(self) -> None:
        self._finalize_agent_run()
        self._set_status("Запрос отменён пользователем")
        if not self._response_output.toPlainText().strip():
            self._response_output.setPlainText("Запрос был отменён пользователем до завершения.")
        self._clear_artifacts()

    def _handle_models_ready(self, models: List[OpenRouterModelInfo]) -> None:
        self._refresh_models_button.setEnabled(True)
        vendor_count = len({model.vendor for model in models})
        self._set_status(f"Получено моделей: {len(models)} | производителей: {vendor_count}")
        self._apply_model_metadata(models)

    def _handle_models_failed(self, message: str) -> None:
        self._refresh_models_button.setEnabled(True)
        self._set_status("Не удалось загрузить модели", error=True)
        self._response_output.setPlainText(message)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _collect_openrouter_overrides(self) -> Dict[str, Any]:
        api_key = self._api_key_edit.text().strip()
        model_info = self._model_combo.currentData()
        if isinstance(model_info, OpenRouterModelInfo):
            model = model_info.model_id
        else:
            selected_text = self._model_combo.currentText().strip()
            model = selected_text if selected_text and self._model_combo.isEnabled() else ""
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

    def _apply_model_metadata(self, models: List[OpenRouterModelInfo]) -> None:
        if not models:
            self._model_combo.clear()
            self._vendor_combo.clear()
            self._model_combo.addItem("Нет моделей")
            self._vendor_combo.addItem("Неизвестно")
            self._model_combo.setEnabled(False)
            self._vendor_combo.setEnabled(False)
            self._update_model_capabilities_hint()
            return

        self._model_combo.setEnabled(True)
        self._vendor_combo.setEnabled(True)
        grouped: Dict[str, List[OpenRouterModelInfo]] = {}
        for model in models:
            grouped.setdefault(model.vendor, []).append(model)
        for vendor, items in grouped.items():
            items.sort(key=lambda info: info.display_name.lower())
        self._models_by_vendor = grouped
        self._rebuild_vendor_combo(self._pending_vendor_key, self._pending_model_id)

    def _rebuild_vendor_combo(
        self,
        preferred_vendor: Optional[str] = None,
        preferred_model: Optional[str] = None,
    ) -> None:
        self._vendor_combo.blockSignals(True)
        self._vendor_combo.clear()
        for vendor in sorted(self._models_by_vendor.keys()):
            label = vendor.replace("-", " ").title()
            self._vendor_combo.addItem(label, vendor)
        self._vendor_combo.blockSignals(False)
        if not self._models_by_vendor:
            self._model_combo.clear()
            self._update_model_capabilities_hint()
            return

        target_index: Optional[int] = None
        if preferred_vendor:
            for idx in range(self._vendor_combo.count()):
                if self._vendor_combo.itemData(idx) == preferred_vendor:
                    target_index = idx
                    break
        if target_index is None and self._vendor_combo.count():
            target_index = 0
        if target_index is not None:
            self._vendor_combo.setCurrentIndex(target_index)
            vendor_key = self._vendor_combo.itemData(target_index)
            preferred_model_id = preferred_model if preferred_vendor == vendor_key else None
            self._populate_models_for_vendor(vendor_key, preferred_model_id)
        else:
            self._model_combo.clear()
            self._update_model_capabilities_hint()

    def _populate_models_for_vendor(
        self, vendor_key: Optional[str], preferred_model_id: Optional[str] = None
    ) -> None:
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        target_index: Optional[int] = None
        if vendor_key and vendor_key in self._models_by_vendor:
            for idx, model in enumerate(self._models_by_vendor[vendor_key]):
                self._model_combo.addItem(model.display_name, model)
                if preferred_model_id and model.model_id == preferred_model_id:
                    target_index = idx
        self._model_combo.blockSignals(False)
        if target_index is not None:
            self._model_combo.setCurrentIndex(target_index)
        elif self._model_combo.count():
            self._model_combo.setCurrentIndex(0)
        else:
            self._update_model_capabilities_hint()

    def _on_vendor_changed(self, index: int) -> None:  # pylint: disable=unused-argument
        vendor_key = self._vendor_combo.currentData()
        self._populate_models_for_vendor(vendor_key)
        self._persist_selected_vendor(vendor_key)

    def _on_model_changed(self, index: int = -1) -> None:  # pylint: disable=unused-argument
        self._update_model_capabilities_hint()
        self._persist_selected_model()

    def _update_model_capabilities_hint(self, index: int = -1) -> None:  # pylint: disable=unused-argument
        model_info = self._model_combo.currentData()
        if isinstance(model_info, OpenRouterModelInfo):
            if model_info.supports_images is True:
                hint = "Поддержка изображений: есть"
            elif model_info.supports_images is False:
                hint = "Поддержка изображений: нет"
            else:
                hint = "Поддержка изображений: неизвестно"
        else:
            hint = "Поддержка изображений: неизвестно"
        self._model_capabilities_label.setText(hint)

    def _seed_default_models(self) -> None:
        defaults = [
            OpenRouterModelInfo("openai/gpt-4o-mini", "openai", "gpt-4o-mini", supports_images=True),
            OpenRouterModelInfo(
                "anthropic/claude-3.5-sonnet",
                "anthropic",
                "claude-3.5-sonnet",
                supports_images=True,
            ),
            OpenRouterModelInfo(
                "meta-llama/llama-3.1-70b-instruct",
                "meta",
                "llama-3.1-70b-instruct",
                supports_images=False,
            ),
        ]
        self._apply_model_metadata(defaults)

    def _load_persistent_settings(self) -> None:
        api_key = self._settings.value("openrouter/api_key")
        if isinstance(api_key, str) and api_key:
            self._api_key_edit.setText(api_key)
        vendor = self._settings.value("openrouter/vendor")
        if isinstance(vendor, str) and vendor:
            self._pending_vendor_key = vendor
        model_id = self._settings.value("openrouter/model_id")
        if isinstance(model_id, str) and model_id:
            self._pending_model_id = model_id

    def _load_iteration_default(self) -> None:
        stored = self._settings.value("pipeline/max_iterations")
        value: Optional[int] = None
        if isinstance(stored, int):
            value = stored
        elif isinstance(stored, str):
            try:
                value = int(stored)
            except ValueError:
                value = None
        if value is None:
            value = self._read_iterations_from_config()
        self._iteration_spin.setValue(max(1, min(20, value)))

    def _persist_iteration_count(self) -> None:
        self._settings.setValue("pipeline/max_iterations", self._iteration_spin.value())

    def _read_iterations_from_config(self) -> int:
        try:
            config = load_config(self._config_path)
            return config.pipeline.max_iterations
        except Exception:  # pragma: no cover - loading config is best-effort
            return 3

    def _persist_api_key(self) -> None:
        api_key = self._api_key_edit.text().strip()
        if api_key:
            self._settings.setValue("openrouter/api_key", api_key)
        else:
            self._settings.remove("openrouter/api_key")

    def _persist_selected_vendor(self, vendor_key: Optional[str]) -> None:
        if self._suspend_selection_persistence:
            return
        if isinstance(vendor_key, str) and vendor_key:
            self._pending_vendor_key = vendor_key
            self._settings.setValue("openrouter/vendor", vendor_key)
        else:
            self._pending_vendor_key = None
            self._settings.remove("openrouter/vendor")

    def _persist_selected_model(self) -> None:
        if self._suspend_selection_persistence:
            return
        model_info = self._model_combo.currentData()
        if isinstance(model_info, OpenRouterModelInfo):
            self._pending_model_id = model_info.model_id
            self._settings.setValue("openrouter/model_id", model_info.model_id)
            vendor_key = self._vendor_combo.currentData()
            if isinstance(vendor_key, str):
                self._persist_selected_vendor(vendor_key)
        else:
            self._settings.remove("openrouter/model_id")
            self._pending_model_id = None

    def _attempt_initial_model_refresh(self) -> None:
        api_key = self._api_key_edit.text().strip()
        if not api_key:
            return
        self._refresh_models()

    def _set_status(self, text: str, error: bool = False) -> None:
        self._status_label.setText(text)
        self._status_label.setStyleSheet("color: red" if error else "")

    def _clear_artifacts(self) -> None:
        self._artifact_data = []
        self._artifact_list.clear()
        self._script_preview.clear()
        if hasattr(self, "_run_selected_button"):
            self._run_selected_button.setEnabled(False)

    def _finalize_agent_run(self) -> None:
        self._run_button.setEnabled(True)
        self._cancel_button.setEnabled(False)
        self._cancel_event = None

    def _update_artifact_list(self, summary: Dict[str, Any]) -> None:
        artifacts = summary.get("artifacts") if isinstance(summary, dict) else None
        if not isinstance(artifacts, list):
            self._clear_artifacts()
            return
        self._artifact_list.clear()
        self._artifact_data = artifacts
        for artifact in artifacts:
            script_path = artifact.get("script", "")
            iteration = artifact.get("iteration", "?")
            objects = artifact.get("affected_objects") or []
            if objects:
                object_text = ", ".join(objects)
            else:
                object_text = "Объекты не обнаружены"
            file_name = Path(script_path).name if script_path else "<неизвестно>"
            item_text = f"Итерация {iteration}: {file_name} — {object_text}"
            item = QtWidgets.QListWidgetItem(item_text)
            icon = self._success_icon if artifact.get("success") else self._failure_icon
            item.setIcon(icon)
            if artifact.get("error"):
                item.setToolTip(str(artifact.get("error")))
            item.setData(QtCore.Qt.UserRole, artifact)
            self._artifact_list.addItem(item)
        if artifacts:
            self._artifact_list.setCurrentRow(len(artifacts) - 1)

    def _display_selected_artifact(self) -> None:
        item = self._artifact_list.currentItem()
        if not item:
            self._script_preview.clear()
            if hasattr(self, "_run_selected_button"):
                self._run_selected_button.setEnabled(False)
            return
        artifact = item.data(QtCore.Qt.UserRole) or {}
        script_body = artifact.get("script_body") or ""
        if not script_body:
            script_path = artifact.get("script")
            if script_path:
                try:
                    script_body = Path(script_path).read_text(encoding="utf-8")
                except OSError as exc:
                    script_body = f"# Не удалось загрузить файл: {exc}"
        header_lines = []
        iteration = artifact.get("iteration")
        if iteration is not None:
            header_lines.append(f"# Итерация: {iteration}")
        script_path = artifact.get("script")
        if script_path:
            header_lines.append(f"# Файл: {script_path}")
        objects = artifact.get("affected_objects") or []
        if objects:
            header_lines.append("# Объекты: " + ", ".join(objects))
        error = artifact.get("error")
        if error:
            header_lines.append("# Ошибка: " + str(error))
        header = "\n".join(header_lines)
        if header:
            header += "\n\n"
        self._script_preview.setPlainText(f"{header}{script_body}".strip())
        self._run_selected_button.setEnabled(bool(script_body.strip()) or bool(artifact.get("script")))

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

    def _execute_selected_macro(self) -> None:
        artifact = self._get_current_artifact()
        if not artifact:
            self._set_status("Выберите макрос для запуска", error=True)
            return
        script_body = artifact.get("script_body") or ""
        script_path = artifact.get("script") or ""
        if not script_body and script_path:
            try:
                script_body = Path(script_path).read_text(encoding="utf-8")
            except OSError as exc:
                self._set_status(f"Не удалось прочитать макрос: {exc}", error=True)
                return
        if not script_body.strip():
            self._set_status("В макросе нет кода для выполнения", error=True)
            return

        self._run_selected_button.setEnabled(False)
        self._set_status("Выполняется выбранный макрос...")

        iteration = artifact.get("iteration")
        iteration_value = iteration if isinstance(iteration, int) else None
        self._macro_thread = QtCore.QThread(self)
        self._macro_worker = _MacroExecutionWorker(script_body, self._config_path, iteration_value)
        self._macro_worker.moveToThread(self._macro_thread)
        self._macro_thread.started.connect(self._macro_worker.run)
        self._macro_worker.finished.connect(self._handle_manual_macro_finished)
        self._macro_worker.failed.connect(self._handle_manual_macro_failed)
        self._macro_worker.finished.connect(self._macro_thread.quit)
        self._macro_worker.failed.connect(self._macro_thread.quit)
        self._macro_thread.finished.connect(self._cleanup_macro_worker)
        self._macro_thread.start()

    def _handle_manual_macro_finished(self, result: Dict[str, Any]) -> None:
        success = bool(result.get("success"))
        status = "Макрос выполнен" if success else "Макрос завершился с ошибкой"
        self._set_status(status, error=not success)
        header_lines = ["# Результат ручного запуска"]
        script_path = result.get("script_path")
        if script_path:
            header_lines.append(f"# Файл: {script_path}")
        error = result.get("error")
        if error:
            header_lines.append(f"# Ошибка: {error}")
        objects = result.get("affected_objects") or []
        if objects:
            header_lines.append("# Объекты: " + ", ".join(objects))
        log_lines = result.get("output_log") or []
        text = "\n".join(header_lines)
        if log_lines:
            text = f"{text}\n\n" + "\n".join(log_lines)
        self._response_output.setPlainText(text.strip())
        self._run_selected_button.setEnabled(True)

    def _handle_manual_macro_failed(self, message: str) -> None:
        self._set_status("Ошибка выполнения макроса", error=True)
        self._response_output.setPlainText(message)
        self._run_selected_button.setEnabled(True)

    def _cleanup_macro_worker(self) -> None:
        if self._macro_worker:
            self._macro_worker.deleteLater()
            self._macro_worker = None
        if self._macro_thread:
            self._macro_thread.deleteLater()
            self._macro_thread = None

    def _get_current_artifact(self) -> Optional[Dict[str, Any]]:
        item = self._artifact_list.currentItem()
        if not item:
            return None
        artifact = item.data(QtCore.Qt.UserRole)
        if isinstance(artifact, dict):
            return artifact
        return None


class _AgentWorker(QtCore.QObject):
    """Executes the DesignAgent in a background thread."""

    finished = QtCore.Signal(dict)
    failed = QtCore.Signal(str)
    cancelled = QtCore.Signal()

    def __init__(
        self,
        requirement: str,
        config_path: Optional[Path],
        llm_overrides: Optional[Dict[str, Any]] = None,
        iteration_limit: Optional[int] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        super().__init__()
        self._requirement = requirement
        self._config_path = config_path
        self._llm_overrides = llm_overrides or {}
        self._iteration_limit = iteration_limit
        self._cancel_event = cancel_event

    @QtCore.Slot()
    def run(self) -> None:
        try:
            config = load_config(self._config_path)
            self._apply_llm_overrides(config)
            if isinstance(self._iteration_limit, int) and self._iteration_limit > 0:
                config.pipeline.max_iterations = self._iteration_limit
            agent = DesignAgent(config)
            report = agent.run(self._requirement, is_cancelled=self._is_cancelled)
            self.finished.emit(_report_to_summary(report))
        except PipelineCancelledError:
            self.cancelled.emit()
        except Exception:  # pragma: no cover - defensive
            self.failed.emit(traceback.format_exc())

    def _is_cancelled(self) -> bool:
        return bool(self._cancel_event and self._cancel_event.is_set())

    def _apply_llm_overrides(self, config: AppConfig) -> None:
        if not self._llm_overrides:
            return
        for key, value in self._llm_overrides.items():
            if value is None:
                continue
            if hasattr(config.llm, key):
                setattr(config.llm, key, value)


class _MacroExecutionWorker(QtCore.QObject):
    """Executes a single macro body using the embedded FreeCAD engine."""

    finished = QtCore.Signal(dict)
    failed = QtCore.Signal(str)

    def __init__(
        self,
        script_body: str,
        config_path: Optional[Path],
        iteration: Optional[int] = None,
    ) -> None:
        super().__init__()
        self._script_body = script_body
        self._config_path = config_path
        self._iteration = iteration or 0

    @QtCore.Slot()
    def run(self) -> None:
        try:
            config = load_config(self._config_path)
            engine = FreeCADEngine(config.freecad, config.pipeline.workspace)
            result = engine.run_script(self._script_body, iteration=max(0, self._iteration))
            payload = {
                "success": result.success,
                "error": result.error,
                "output_log": result.output_log,
                "script_path": str(result.script_path),
                "affected_objects": result.affected_objects,
            }
            self.finished.emit(payload)
        except Exception:  # pragma: no cover - defensive
            self.failed.emit(traceback.format_exc())


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
                "script_body": artifact.script_body,
                "renders": [str(path) for path in artifact.render_paths],
                "success": artifact.success,
                "error": artifact.error,
                "render_feedback": artifact.render_feedback,
                "output_log": artifact.output_log,
                "affected_objects": artifact.affected_objects,
            }
        )
    return {"success": report.successful, "artifacts": artifacts, "requirement": report.requirement}


__all__ = ["AgentDockWidget"]
