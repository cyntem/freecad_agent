from pathlib import Path

from freecad_llm_agent.config import load_config, AppConfig
from freecad_llm_agent.pipeline import DesignAgent


def test_pipeline_produces_artifacts(tmp_path: Path, monkeypatch):
    config = AppConfig()
    config.pipeline.workspace = tmp_path / "artifacts"
    config.renderer.image_dir = config.pipeline.workspace / "renders"
    agent = DesignAgent(config)
    report = agent.run("Тестовое задание: создать корпус.")
    assert report.artifacts, "Agent must produce at least one iteration"
    assert report.artifacts[0].script_path.exists()
    for artifact in report.artifacts:
        for render in artifact.render_paths:
            assert Path(render).exists()
