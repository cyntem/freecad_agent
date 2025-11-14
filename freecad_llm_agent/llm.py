"""LLM client abstractions used by the agent."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

logger = logging.getLogger(__name__)


@dataclass
class Message:
    """Single chat message."""

    role: str
    content: str


class LLMClient(ABC):
    """Minimal interface that all LLM providers must implement."""

    @abstractmethod
    def complete(self, messages: Sequence[Message], images: Optional[Iterable[str]] = None) -> str:
        """Return a text completion for the provided conversation history."""


class DummyLLMClient(LLMClient):
    """Deterministic offline implementation used for local development."""

    def __init__(self, model: str = "dummy", temperature: float = 0.0) -> None:
        self.model = model
        self.temperature = temperature

    def complete(self, messages: Sequence[Message], images: Optional[Iterable[str]] = None) -> str:
        prompt = "\n".join(f"{m.role}: {m.content}" for m in messages)
        logger.debug("Dummy LLM received prompt: %s", prompt)
        if "assembly" in prompt.lower():
            return self._assembly_template(prompt)
        if "error" in prompt.lower():
            return self._repair_template(prompt)
        return self._default_template(prompt)

    def _default_template(self, prompt: str) -> str:
        """Return a FreeCAD macro that models a simple block."""

        return (
            "import FreeCAD as App\n"
            "import Part\n"
            "doc = App.newDocument('LLMAgentModel')\n"
            "box = Part.makeBox(10, 20, 30)\n"
            "part_obj = doc.addObject('Part::Feature', 'GeneratedBlock')\n"
            "part_obj.Shape = box\n"
            "doc.recompute()\n"
            "App.ActiveDocument = doc\n"
            "print('Model generated successfully')"
        )

    def _assembly_template(self, prompt: str) -> str:
        return (
            "import FreeCAD as App\n"
            "import Assembly4\n"
            "doc = App.newDocument('AssemblyDoc')\n"
            "print('Assembly placeholder created')"
        )

    def _repair_template(self, prompt: str) -> str:
        return self._default_template(prompt) + "\nprint('Applied fix for previous error')"


def dump_messages(messages: Sequence[Message]) -> str:
    """Return a human-readable representation of the conversation."""

    return json.dumps([message.__dict__ for message in messages], ensure_ascii=False, indent=2)


__all__ = ["Message", "LLMClient", "DummyLLMClient", "dump_messages"]
