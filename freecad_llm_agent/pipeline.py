"""High-level orchestration logic for the FreeCAD LLM agent."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence

from .config import AppConfig
from .freecad_runner import FreeCADEngine
from .llm import LLMClient, Message, create_llm_client
from .rendering import RenderResult, Renderer
from .script_generation import ScriptGenerationContext, ScriptGenerator

logger = logging.getLogger(__name__)


@dataclass
class IterationArtifact:
    iteration: int
    script_path: Path
    script_body: str
    output_log: List[str]
    render_paths: List[Path]
    success: bool
    error: Optional[str] = None
    render_feedback: Optional[str] = None
    affected_objects: List[str] = field(default_factory=list)


@dataclass
class RenderReview:
    feedback: str
    needs_additional_views: bool = False


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


class PipelineCancelledError(RuntimeError):
    """Raised when the pipeline run is interrupted by the caller."""


class DesignAgent:
    """Main entry point coordinating the LLM, FreeCAD runtime and renderer."""

    def __init__(self, config: AppConfig, llm_client: Optional[LLMClient] = None) -> None:
        self._config = config
        self._llm = llm_client or create_llm_client(config.llm)
        self._generator = ScriptGenerator(self._llm)
        self._engine = FreeCADEngine(config.freecad, config.pipeline.workspace)
        self._renderer = Renderer(config.renderer)

    def run(
        self,
        requirement: str,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> PipelineReport:
        report = PipelineReport(requirement=requirement)
        errors: List[str] = []
        assembly_required = self._require_assembly(requirement)
        pending_additional_views = False
        script_history: List[str] = []

        def _should_cancel() -> bool:
            return bool(is_cancelled and is_cancelled())

        def _ensure_not_cancelled() -> None:
            if _should_cancel():
                raise PipelineCancelledError("Pipeline run was cancelled")

        for iteration in range(1, self._config.pipeline.max_iterations + 1):
            _ensure_not_cancelled()
            logger.info("Starting iteration %s", iteration)
            context = ScriptGenerationContext(
                requirement=requirement,
                previous_errors=list(errors),
                request_additional_views=(
                    (self._config.pipeline.request_additional_views_on_failure and bool(errors))
                    or pending_additional_views
                ),
                requires_assembly=assembly_required,
                script_history=list(script_history),
            )
            script = self._generator.generate(context)
            script_history.append(script)
            _ensure_not_cancelled()
            execution = self._engine.run_script(script, iteration)
            _ensure_not_cancelled()
            renders = self._renderer.render(requirement, iteration)
            _ensure_not_cancelled()
            review = self._review_renders(requirement, iteration, renders, execution.success)
            pending_additional_views = pending_additional_views or review.needs_additional_views
            artifact = IterationArtifact(
                iteration=iteration,
                script_path=execution.script_path,
                script_body=script,
                output_log=execution.output_log,
                render_paths=[render.image_path for render in renders],
                success=execution.success,
                error=execution.error,
                render_feedback=review.feedback,
                affected_objects=execution.affected_objects,
            )
            report.artifacts.append(artifact)

            if execution.success:
                logger.info("Iteration %s succeeded", iteration)
                break

            logger.warning("Iteration %s failed: %s", iteration, execution.error)
            errors.append(self._format_execution_feedback(iteration, execution))
            _ensure_not_cancelled()

        return report

    def _require_assembly(self, requirement: str) -> bool:
        lowered = requirement.lower()
        return any(keyword in lowered for keyword in ["assembly", "assemblies", "сборк"])

    def _review_renders(
        self,
        requirement: str,
        iteration: int,
        renders: Iterable[RenderResult],
        success: bool,
    ) -> RenderReview:
        image_paths = [str(render.image_path) for render in renders]
        if not image_paths:
            return RenderReview(feedback="Rendering disabled")

        system_prompt = (
            "You are a manufacturing inspector reviewing rendered CAD previews. "
            "Respond with JSON containing 'needs_additional_views' (true/false) and 'feedback'."
        )
        user_prompt = "\n".join(
            [
                "=== RENDER REVIEW ===",
                f"Requirement: {requirement.strip()}",
                f"Iteration: {iteration}",
                f"Succeeded: {success}",
                "If geometry is unclear request additional projections.",
            ]
        )
        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ]
        try:
            response = self._llm.complete(messages, images=image_paths)
            return self._parse_render_response(response)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("Render review failed: %s", exc)
            return RenderReview(feedback=f"Render review failed: {exc}")

    def _parse_render_response(self, response: str) -> RenderReview:
        try:
            data = json.loads(response)
            needs_more = bool(data.get("needs_additional_views"))
            feedback = str(data.get("feedback", "")) or "LLM render review complete"
            return RenderReview(feedback=feedback, needs_additional_views=needs_more)
        except json.JSONDecodeError:
            lowered = response.lower()
            needs_more = "additional" in lowered or "extra view" in lowered
        feedback = response.strip() or "Render review response received"
        return RenderReview(feedback=feedback, needs_additional_views=needs_more)

    def _format_execution_feedback(self, iteration: int, execution: "ScriptExecutionResult") -> str:
        lines = [f"Iteration {iteration} FreeCAD execution failed."]
        if execution.error:
            lines.append(f"Error: {execution.error}")
        if execution.output_log:
            lines.append("Recent FreeCAD output:")
            lines.extend(self._tail_lines(execution.output_log))
        if not execution.error and not execution.output_log:
            lines.append("No output was captured before the failure.")
        return "\n".join(lines)

    def _tail_lines(self, log_lines: Sequence[str], limit: int = 40) -> List[str]:
        lines = list(log_lines)
        if len(lines) <= limit:
            return lines
        truncated = len(lines) - limit
        return [f"... truncated {truncated} earlier lines ...", *lines[-limit:]]


__all__ = [
    "DesignAgent",
    "PipelineReport",
    "IterationArtifact",
    "RenderReview",
    "PipelineCancelledError",
]
