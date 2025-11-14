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
