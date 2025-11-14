"""Tools that build prompts and ask the LLM to output FreeCAD Python macros."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Sequence

from .llm import LLMClient, Message


@dataclass
class EnvironmentInfo:
    """Metadata about the currently available FreeCAD installation."""

    freecad_version: str = "0.21"
    workbenches: Sequence[str] = (
        "Part",
        "Sketcher",
        "TechDraw",
        "Assembly3",
        "Assembly4",
        "A2plus",
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
                    "that follow FreeCAD API best practices and always call doc.recompute()."
                ),
            ),
            Message(
                role="user",
                content=self._build_user_prompt(context),
            ),
        ]
        return self._llm.complete(messages)

    def _build_user_prompt(self, context: ScriptGenerationContext) -> str:
        lines = [
            "=== DESIGN REQUIREMENT ===",
            context.requirement.strip(),
            "",
            "=== ENVIRONMENT ===",
            f"FreeCAD version: {context.environment.freecad_version}",
            f"Installed workbenches: {', '.join(context.environment.workbenches)}",
            f"Notes: {context.environment.notes}",
            "",
        ]
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
        lines.append("Return only Python code without markdown fences.")
        return "\n".join(lines)


__all__ = ["EnvironmentInfo", "ScriptGenerationContext", "ScriptGenerator"]
