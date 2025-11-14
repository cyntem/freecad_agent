# FreeCAD LLM Agent

Автономный агент, который генерирует, исполняет и анализирует Python-скрипты для FreeCAD на основе текстового ТЗ.

## Возможности
- Принимает текстовое задание и строит по нему Python-скрипт для FreeCAD с помощью LLM.
- Сохраняет макросы и журнал выполнения по каждой итерации.
- Создаёт плейсхолдерные рендеры (PNG) для последующего анализа модели LLM c поддержкой изображений.
- Поддерживает несколько итераций, анализируя ошибки и запрашивая дополнительные проекции при необходимости.
- Архитектура учитывает работу с деталями и сборками (Assembly3/4/A2plus) и может быть расширена под реальные вызовы FreeCAD.

## Структура проекта
```
freecad_llm_agent/
  config.py            # Конфигурация и загрузка YAML/JSON
  llm.py               # Абстракция LLM и детерминированный dummy-клиент
  script_generation.py # Построение промптов и запросов к LLM
  freecad_runner.py    # Запуск и симуляция макросов FreeCAD
  rendering.py         # Генерация рендеров/заглушек
  pipeline.py          # Оркестратор итераций агента
main.py                # CLI-интерфейс
requirements.txt
scripts/install_linux.sh
scripts/install_windows.ps1
```

## Установка зависимостей
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Для базовой установки FreeCAD и Python можно воспользоваться скриптами в `scripts/`:
- Linux: `scripts/install_linux.sh` (apt)
- Windows: `scripts/install_windows.ps1` (choco)

## Конфигурация
Создайте `config.yml` (пример):
```yaml
freecad:
  executable_path: /usr/bin/freecadcmd
renderer:
  width: 1024
  height: 768
pipeline:
  max_iterations: 3
```

Все пути в конфиге нормализуются относительно рабочей директории. При отсутствии файла используются значения по умолчанию.

## Использование
```bash
python main.py "Создать корпус редуктора с посадочными местами"
```
или
```bash
python main.py requirement.txt --is-file
```

В результате CLI вернёт JSON cо списком итераций и путями к сгенерированным скриптам/рендерам в каталоге `artifacts/`.

## Тестирование
В качестве smoke-теста можно запустить:
```bash
python -m pytest
```
(см. тесты в каталоге `tests/`).

## Дальнейшее развитие
- Подключение реального LLM API (OpenAI, Azure, локальные модели) через реализацию `LLMClient`.
- Интеграция с настоящим FreeCAD (вызов `freecadcmd` и анализ журналов).
- Обработка результатов рендеров моделью с поддержкой изображений и автоматический запрос дополнительных проекций.
- Импорт сборок через Assembly3/Assembly4/A2plus и управление зависимостями деталей.
