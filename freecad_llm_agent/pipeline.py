"""High-level orchestration logic for the FreeCAD LLM agent."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .config import AppConfig
from .freecad_runner import FreeCADEngine
from .llm import DummyLLMClient, LLMClient, Message
from .rendering import Renderer
from .script_generation import ScriptGenerationContext, ScriptGenerator

logger = logging.getLogger(__name__)


@dataclass
class IterationArtifact:
    iteration: int
    script_path: Path
    output_log: List[str]
    render_paths: List[Path]
    success: bool
    error: Optional[str] = None


@dataclass
class PipelineReport:
    requirement: str
    artifacts: List[IterationArtifact] = field(default_factory=list)

    @property
    def successful(self) -> bool:
        return any(artifact.success for artifact in self.artifacts)

    @property
    def last_error(self) -> Optional[str]:
        for artifact in reversed(self.artifacts):
            if artifact.error:
                return artifact.error
        return None


class DesignAgent:
    """Main entry point coordinating the LLM, FreeCAD runtime and renderer."""

    def __init__(self, config: AppConfig, llm_client: Optional[LLMClient] = None) -> None:
        self._config = config
        self._llm = llm_client or DummyLLMClient(model=config.llm.model, temperature=config.llm.temperature)
        self._generator = ScriptGenerator(self._llm)
        self._engine = FreeCADEngine(config.freecad, config.pipeline.workspace)
        self._renderer = Renderer(config.renderer)

    def run(self, requirement: str) -> PipelineReport:
        report = PipelineReport(requirement=requirement)
        errors: List[str] = []

        for iteration in range(1, self._config.pipeline.max_iterations + 1):
            logger.info("Starting iteration %s", iteration)
            context = ScriptGenerationContext(
                requirement=requirement,
                previous_errors=list(errors),
                request_additional_views=(
                    self._config.pipeline.request_additional_views_on_failure and bool(errors)
                ),
            )
            script = self._generator.generate(context)
            execution = self._engine.run_script(script, iteration)
            renders = self._renderer.render(requirement, iteration)
            artifact = IterationArtifact(
                iteration=iteration,
                script_path=execution.script_path,
                output_log=execution.output_log,
                render_paths=[render.image_path for render in renders],
                success=execution.success,
                error=execution.error,
            )
            report.artifacts.append(artifact)

            if execution.success:
                logger.info("Iteration %s succeeded", iteration)
                break

            logger.warning("Iteration %s failed: %s", iteration, execution.error)
            if execution.error:
                errors.append(execution.error)

        return report


__all__ = ["DesignAgent", "PipelineReport", "IterationArtifact"]
