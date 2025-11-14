# FreeCAD LLM Agent

Автономный агент, который генерирует, исполняет и анализирует Python-скрипты для FreeCAD на основе текстового ТЗ.

## Возможности
- Принимает текстовое задание и строит по нему Python-скрипт для FreeCAD с помощью LLM (поддерживаются провайдеры OpenAI, Azure OpenAI и локальные OpenAI-совместимые API).
- Сохраняет макросы, журналы выполнения и дополнительные артефакты по каждой итерации.
- Запускает реальный `freecadcmd`, собирает stdout/stderr и анализирует журналы для поиска ошибок.
- Создаёт рендеры (PNG) и передаёт их в LLM с поддержкой изображений для автоматического запроса дополнительных проекций.
- Промпты учитывают требования к сборкам: импорт через Assembly3/Assembly4/A2plus и управление зависимостями деталей.

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
- Интеграция с системами управления данными изделия (PDM/PLM).
- Экспорт результатов в форматы STEP/TechDraw.
- Улучшение диагностики качества сетки и столкновений.
