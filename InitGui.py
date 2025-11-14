"""Registers the LLM agent as a FreeCAD workbench."""

try:  # pragma: no cover - executed only inside FreeCAD
    import FreeCADGui  # type: ignore
except ImportError:  # pragma: no cover
    FreeCADGui = None  # type: ignore


if FreeCADGui:  # pragma: no cover - GUI hooks

    class LLMAgentWorkbench(FreeCADGui.Workbench):
        MenuText = "LLM Agent"
        ToolTip = "Генерация и запуск макросов FreeCAD через LLM"

        def Initialize(self) -> None:
            pass

        def Activated(self) -> None:
            from freecad_llm_agent.freecad_extension import show_agent_dock_widget

            show_agent_dock_widget()

        def Deactivated(self) -> None:  # pragma: no cover - optional hook
            pass

        def GetClassName(self) -> str:
            return "Gui::PythonWorkbench"

    FreeCADGui.addWorkbench(LLMAgentWorkbench())

