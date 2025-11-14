"""Tools that build prompts and ask the LLM to output FreeCAD Python macros."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Sequence

from .llm import LLMClient, Message


@dataclass(frozen=True)
class ExtensionInfo:
    """Description of a FreeCAD workbench/extension available to the agent."""

    name: str
    version: str

    def label(self) -> str:
        version = self.version.strip()
        return f"{self.name} (v{version})" if version else self.name


@dataclass
class EnvironmentInfo:
    """Metadata about the currently available FreeCAD installation."""

    freecad_version: str = "0.21"
    extensions: Sequence[ExtensionInfo] = (
        ExtensionInfo("Part", "0.21"),
        ExtensionInfo("Sketcher", "0.21"),
        ExtensionInfo("TechDraw", "1.0"),
        ExtensionInfo("Assembly3", "0.12"),
        ExtensionInfo("Assembly4", "0.50"),
        ExtensionInfo("A2plus", "0.4"),
    )
    notes: str = "Headless mode with automatic recompute"


@dataclass
class ScriptGenerationContext:
    """Information passed to the LLM during prompt construction."""

    requirement: str
    previous_errors: List[str] = field(default_factory=list)
    environment: EnvironmentInfo = field(default_factory=EnvironmentInfo)
    request_additional_views: bool = False
    requires_assembly: bool = False
    script_history: List[str] = field(default_factory=list)


class ScriptGenerator:
    """Wrap LLM prompting details for better unit testing."""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def generate(self, context: ScriptGenerationContext) -> str:
        messages = [
            Message(
                role="system",
                content=(
                    "You are a FreeCAD automation expert. Generate executable Python macros "
                    "that follow FreeCAD API best practices, always call doc.recompute(), and "
                    "ensure the active project document ('LLMAgentProject') stays visible so the "
                    "3D preview updates while the macro runs."
                ),
            ),
            Message(
                role="user",
                content=self._build_user_prompt(context),
            ),
        ]
        return self._llm.complete(messages)

    def _build_user_prompt(self, context: ScriptGenerationContext) -> str:
        extensions = list(context.environment.extensions)
        extension_line = (
            "Installed extensions: "
            + (", ".join(extension.label() for extension in extensions) if extensions else "None")
        )

        lines = [
            "=== DESIGN REQUIREMENT ===",
            context.requirement.strip(),
            "",
            "=== ENVIRONMENT ===",
            f"FreeCAD version: {context.environment.freecad_version}",
            extension_line,
            f"Notes: {context.environment.notes}",
            "",
        ]
        if context.script_history:
            lines.append("=== PREVIOUS PYTHON CONTEXT ===")
            history_limit = 3
            history_slice = context.script_history[-history_limit:]
            start_index = len(context.script_history) - len(history_slice) + 1
            for offset, script in enumerate(history_slice):
                lines.append(f"[Script {start_index + offset}]")
                snippet = script.strip() or "# Empty script recorded"
                lines.append(snippet)
                lines.append("")
        if context.previous_errors:
            lines.extend(["=== PREVIOUS ERRORS ===", *context.previous_errors, ""])
        if context.request_additional_views:
            lines.append("Render additional projections for better inspection.")
        if context.requires_assembly:
            lines.extend(
                [
                    "",
                    "The requirement references an assembly. Import dependent parts using Assembly3/Assembly4/A2plus",
                    "workbenches and ensure each sub-component document is loaded before constraints are solved.",
                ]
            )
        lines.extend(
            [
                "=== DOCUMENT WORKFLOW RULES ===",
                "Reuse FreeCAD.ActiveDocument and create a document named 'LLMAgentProject' if it does not exist.",
                "Add new Bodies/Parts to the active document, keep it active, and avoid deleting previously generated geometry.",
                "Ensure the preview updates by calling doc.recompute() and, when Gui is available, fit the active view.",
                "Return only Python code without markdown fences.",
            ]
        )
        return "\n".join(lines)


__all__ = ["ExtensionInfo", "EnvironmentInfo", "ScriptGenerationContext", "ScriptGenerator"]
