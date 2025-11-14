from pathlib import Path

import pytest

from freecad_llm_agent.config import load_config, AppConfig
from freecad_llm_agent.freecad_runner import ScriptExecutionResult
from freecad_llm_agent.pipeline import DesignAgent, RenderReview, PipelineCancelledError


class RecordingGenerator:
    def __init__(self, scripts):
        self._scripts = scripts
        self.contexts = []

    def generate(self, context):  # type: ignore[no-untyped-def]
        self.contexts.append(context)
        index = len(self.contexts) - 1
        return self._scripts[index]


class FailingThenPassingEngine:
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self.calls = 0

    def run_script(self, script_body, iteration):  # type: ignore[no-untyped-def]
        self.calls += 1
        script_path = self._workspace / f"iteration_{iteration}.py"
        script_path.write_text(script_body, encoding="utf-8")
        if self.calls == 1:
            return ScriptExecutionResult(
                success=False,
                script_path=script_path,
                output_log=["line1", "line2"],
                error="boom",
                affected_objects=[],
            )
        return ScriptExecutionResult(True, script_path, ["ok"], None, [])


class NoopRenderer:
    def render(self, requirement, iteration):  # type: ignore[no-untyped-def]
        return []


def test_pipeline_produces_artifacts(tmp_path: Path, monkeypatch):
    config = AppConfig()
    config.pipeline.workspace = tmp_path / "artifacts"
    config.renderer.image_dir = config.pipeline.workspace / "renders"
    agent = DesignAgent(config)
    report = agent.run("Тестовое задание: создать корпус.")
    assert report.artifacts, "Agent must produce at least one iteration"
    assert report.artifacts[0].script_path.exists()
    assert report.artifacts[0].script_body.strip(), "Script body must be preserved for context"
    assert report.artifacts[0].script_path.read_text(encoding="utf-8") == report.artifacts[0].script_body
    for artifact in report.artifacts:
        for render in artifact.render_paths:
            assert Path(render).exists()


def test_failed_execution_feedback_is_shared_with_llm(tmp_path: Path):
    config = AppConfig()
    config.pipeline.workspace = tmp_path / "artifacts"
    config.renderer.image_dir = config.pipeline.workspace / "renders"
    agent = DesignAgent(config)
    generator = RecordingGenerator(["print('first')", "print('second')"])
    agent._generator = generator  # type: ignore[assignment]
    agent._engine = FailingThenPassingEngine(config.pipeline.workspace)
    agent._renderer = NoopRenderer()

    def fake_review(*args, **kwargs):  # type: ignore[no-untyped-def]
        return RenderReview(feedback="ok")

    agent._review_renders = fake_review  # type: ignore[assignment]

    report = agent.run("Тест с ошибкой")
    assert report.artifacts, "Pipeline should record iterations"
    assert len(generator.contexts) >= 2, "Two iterations expected (failure + retry)"
    second_context = generator.contexts[1]
    assert second_context.previous_errors, "Next iteration must receive previous error context"
    combined_feedback = "\n".join(second_context.previous_errors)
    assert "boom" in combined_feedback
    assert "line2" in combined_feedback, "Execution log should be included in feedback"


def test_pipeline_respects_cancellation(tmp_path: Path):
    config = AppConfig()
    config.pipeline.workspace = tmp_path / "artifacts"
    config.renderer.image_dir = config.pipeline.workspace / "renders"
    agent = DesignAgent(config)
    generator = RecordingGenerator(["print('first')"])
    agent._generator = generator  # type: ignore[assignment]
    agent._engine = FailingThenPassingEngine(config.pipeline.workspace)
    agent._renderer = NoopRenderer()

    with pytest.raises(PipelineCancelledError):
        agent.run("Отмена после генерации", is_cancelled=lambda: bool(generator.contexts))
