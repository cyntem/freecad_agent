from types import SimpleNamespace

from freecad_llm_agent.config import FreeCADConfig
from freecad_llm_agent.freecad_runner import FreeCADEngine


class _DummyDoc:
    def __init__(self) -> None:
        self.recompute_calls = 0

    def recompute(self) -> None:
        self.recompute_calls += 1


class _DummyView:
    def __init__(self) -> None:
        self.fit_calls = 0
        self.update_calls = 0

    def fitAll(self) -> None:  # noqa: N802 - FreeCAD API naming convention
        self.fit_calls += 1

    def update(self) -> None:
        self.update_calls += 1


def test_embedded_runner_refreshes_gui(monkeypatch, tmp_path):
    import freecad_llm_agent.freecad_runner as runner

    document = _DummyDoc()
    view = _DummyView()
    updates: list[str] = []
    messages: list[str] = []

    gui_document = SimpleNamespace(ActiveView=view)
    gui_stub = SimpleNamespace(
        ActiveDocument=gui_document,
        updateGui=lambda: updates.append("update"),
        SendMsgToActiveView=lambda message: messages.append(message),
    )

    monkeypatch.setattr(runner, "FreeCAD", SimpleNamespace(ActiveDocument=document), raising=False)
    monkeypatch.setattr(runner, "FreeCADGui", gui_stub, raising=False)

    workspace = tmp_path / "workspace"
    config = FreeCADConfig()
    engine = runner.FreeCADEngine(config, workspace)

    result = engine.run_script("print('ok')", iteration=1)

    assert result.success
    assert any("ok" in line for line in result.output_log)
    assert document.recompute_calls >= 1
    assert view.fit_calls >= 1
    assert messages and messages[-1] == "ViewFit"
    assert updates, "FreeCAD GUI should be refreshed"


def test_embedded_runner_uses_gui_active_document(monkeypatch, tmp_path):
    import freecad_llm_agent.freecad_runner as runner

    class _Doc:
        def __init__(self, name: str) -> None:
            self.Name = name
            self.Objects = []
            self.ActiveObject = None

    document = _Doc("GuiDoc")
    gui_document = SimpleNamespace(Document=document, Name=document.Name)

    class _FreeCADStub:
        def __init__(self) -> None:
            self.ActiveDocument = None
            self.set_calls: list[str] = []

        def setActiveDocument(self, name: str) -> None:
            self.set_calls.append(name)

    freecad_stub = _FreeCADStub()

    def _get_document(name: str):  # noqa: D401 - mimic FreeCADGui API
        assert name == document.Name
        return gui_document

    gui_stub = SimpleNamespace(ActiveDocument=gui_document, getDocument=_get_document)

    monkeypatch.setattr(runner, "FreeCAD", freecad_stub, raising=False)
    monkeypatch.setattr(runner, "FreeCADGui", gui_stub, raising=False)

    runtime = runner._EmbeddedFreeCADRuntime()
    runtime._ensure_project_document()

    assert freecad_stub.ActiveDocument is document
    assert freecad_stub.set_calls and freecad_stub.set_calls[-1] == document.Name


def test_engine_discovers_snap_installation(monkeypatch, tmp_path):
    import freecad_llm_agent.freecad_runner as runner

    workspace = tmp_path / "workspace"
    configured_path = tmp_path / "missing" / "freecadcmd"
    snap_path = tmp_path / "snap" / "bin" / "freecadcmd"
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    snap_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    monkeypatch.delenv("FREECAD_EXECUTABLE", raising=False)
    monkeypatch.setenv("FREECAD_EXECUTABLE", str(snap_path))

    config = FreeCADConfig(executable_path=configured_path)
    engine = runner.FreeCADEngine(config, workspace)

    assert engine._executable == str(snap_path)
