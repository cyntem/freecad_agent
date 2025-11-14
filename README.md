# FreeCAD LLM Agent

An autonomous agent that generates, runs, and analyzes Python scripts for FreeCAD from a textual specification.

## Capabilities
- Consumes a text brief and produces a FreeCAD Python script with the help of an LLM (supports OpenAI, Azure OpenAI, OpenRouter, and self-hosted OpenAI-compatible APIs).
- Saves macros, execution logs, and additional artifacts for every iteration.
- Launches the real `freecadcmd`, captures stdout/stderr, and inspects logs to detect errors.
- Produces renders (PNG) and passes them to a multimodal LLM to automatically request extra projections.
- Prompt templates cover assembly requirements: importing through Assembly3/Assembly4/A2plus and managing part dependencies.
- Ships with a FreeCAD extension that adds a dock widget with text input/output and OpenRouter settings capable of loading the full model list.

## Project structure
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

## Installing dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

For a baseline installation of FreeCAD and Python you can use the helper scripts in `scripts/`:
- Linux: `scripts/install_linux.sh` (apt)
- Windows: `scripts/install_windows.ps1` (choco)

## Configuration
Create a `config.yml` (example):
```yaml
freecad:
  executable_path: /usr/bin/freecadcmd
renderer:
  width: 1024
  height: 768
pipeline:
  max_iterations: 3
```

All paths in the config are normalized relative to the working directory. Default values are used when the file is missing.

LLM providers accept additional knobs inside the `llm` block to cope with rate limits:

```yaml
llm:
  provider: openrouter
  api_key: sk-...
  request_timeout: 120  # seconds per HTTP call
  max_retries: 5        # number of attempts per request
  retry_backoff: 2.0    # exponential backoff multiplier when Retry-After is absent
```

Increasing the retry budget is helpful when OpenRouter returns HTTP 429 responses (Too Many Requests).

## Usage
```bash
python main.py "Создать корпус редуктора с посадочными местами"
```
or
```bash
python main.py requirement.txt --is-file
```

The CLI returns JSON with the list of iterations and paths to the generated scripts/renders inside the `artifacts/` directory.

## Installing the FreeCAD extension
1. Clone or download this repository.
2. Locate the FreeCAD `Mod` directory:
   - Linux: `~/.local/share/FreeCAD/Mod`
   - macOS: `~/Library/Preferences/FreeCAD/Mod`
   - Windows: `%APPDATA%/FreeCAD/Mod`
3. Copy the repository (or create a symlink) into that `Mod` folder, e.g. `ln -s /path/to/freecad_agent ~/.local/share/FreeCAD/Mod/LLMAgent` on Linux.
4. Restart FreeCAD so it picks up the new workbench.
5. Open **Tools → Addon manager** (optional) and verify that the **LLM Agent** workbench is listed.

### Using the extension with the Snap build of FreeCAD on Linux
When FreeCAD is installed via Snap, the sandboxed environment uses a different configuration path. Follow the steps below instead of the generic Linux instructions above:

1. Ensure the Snap package has permission to access your home directory, e.g. `snap connect freecad:removable-media` if you plan to work outside `~/snap/freecad`.
2. Find the Snap-specific `Mod` directory: `~/snap/freecad/current/.local/share/FreeCAD/Mod`.
3. Copy this repository (or create a symlink) into that folder, for example `ln -s /path/to/freecad_agent ~/snap/freecad/current/.local/share/FreeCAD/Mod/LLMAgent`.
4. Restart the Snap instance of FreeCAD and confirm the **LLM Agent** workbench appears under **Tools → Addon manager**.

After these steps the extension becomes available in the FreeCAD UI.

### Installing Python dependencies for the FreeCAD environment
Copying or cloning this repository into the `Mod` directory only makes the UI
available—the Python dependencies listed in `requirements.txt` are not installed
automatically into FreeCAD's embedded interpreter. Install them manually:

**Windows (official installer):**
1. Open **PowerShell** and navigate to the directory where you placed the
   extension, e.g. `cd $env:APPDATA\FreeCAD\Mod\LLMAgent`.
2. Use the Python executable bundled with FreeCAD to install the requirements:
   ```powershell
   "${env:ProgramFiles}\FreeCAD 0.21\bin\python.exe" -m pip install -r requirements.txt
   ```
   Adjust the path if FreeCAD is installed elsewhere.

**Linux (Snap build):**
1. Drop into the sandbox shell so you can run the confined Python interpreter:
   ```bash
   snap run --shell freecad
   ```
2. Inside that shell install the dependencies to the user site directory that
   the Snap build can read:
   ```bash
   cd "$SNAP_USER_COMMON/Mod/LLMAgent"
   "$SNAP/usr/bin/python3" -m pip install --user -r requirements.txt
   ```
3. Type `exit` to leave the Snap shell and restart FreeCAD.

Installing the packages through the FreeCAD-provided Python ensures they end up
in the environment that executes the workbench macros.

## FreeCAD extension and graphical interface
1. Launch FreeCAD ≥0.21 and switch to the **LLM Agent** workbench. The dock widget provides a task input field, an output log, and OpenRouter settings.
2. To connect to OpenRouter, supply your API key and, if needed, `HTTP-Referer` (your application URL) plus `X-Title` (integration name). The **Refresh model list** button calls the `/models` API and lets you choose any available model.
3. Enter a textual requirement and press **Run agent**. Execution happens in the background, the status is shown in the dock, and responses are printed as JSON. If no OpenRouter key is provided, the extension falls back to `config.yml` settings (e.g., local models or OpenAI/Azure).

## Testing
As a smoke test you can run:
```bash
python -m pytest
```
(see the tests inside the `tests/` directory).

## Future work
- Integrate with product data management systems (PDM/PLM).
- Export results to STEP/TechDraw formats.
- Improve mesh quality diagnostics and collision checks.
