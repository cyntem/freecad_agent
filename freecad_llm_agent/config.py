"""Configuration utilities for the FreeCAD LLM agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
import json

try:  # pragma: no cover - optional dependency
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    yaml = None


@dataclass
class FreeCADConfig:
    """Configuration block describing how to interact with FreeCAD."""

    executable_path: Path = Path("/usr/bin/freecadcmd")
    use_builtin_python: bool = False
    headless: bool = True
    timeout_seconds: int = 180


@dataclass
class LLMConfig:
    """Configuration for the LLM provider used by the agent."""

    provider: str = "dummy"
    model: str = "gpt-4o-mini"
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    organization: Optional[str] = None
    openrouter_api_base: Optional[str] = None
    openrouter_site_url: Optional[str] = None
    openrouter_app_name: Optional[str] = None
    azure_endpoint: Optional[str] = None
    azure_deployment: Optional[str] = None
    azure_api_version: str = "2024-02-01"
    local_endpoint: Optional[str] = None
    local_headers: Dict[str, str] = field(default_factory=dict)
    max_tokens: int = 2048
    temperature: float = 0.1


@dataclass
class RendererConfig:
    """Rendering settings for generated FreeCAD geometry."""

    image_dir: Path = Path("artifacts/renders")
    views: Iterable[str] = field(default_factory=lambda: ["isometric", "front", "right", "top"])
    width: int = 1280
    height: int = 720


@dataclass
class PipelineConfig:
    """Settings that control the orchestration loop."""

    max_iterations: int = 5
    request_additional_views_on_failure: bool = True
    workspace: Path = Path("artifacts")


@dataclass
class AppConfig:
    """Container for all configuration sections."""

    freecad: FreeCADConfig = field(default_factory=FreeCADConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    renderer: RendererConfig = field(default_factory=RendererConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppConfig":
        return cls(
            freecad=FreeCADConfig(**data.get("freecad", {})),
            llm=LLMConfig(**data.get("llm", {})),
            renderer=RendererConfig(**data.get("renderer", {})),
            pipeline=PipelineConfig(**data.get("pipeline", {})),
        )


def load_config(path: Optional[Path] = None) -> AppConfig:
    """Load configuration from a YAML or JSON file.

    If ``path`` is ``None`` the function falls back to ``config.yml`` in the
    project root. Missing files result in default configuration values.
    """

    if path is None:
        path = Path("config.yml")

    if not path.exists():
        return AppConfig()

    with path.open("r", encoding="utf-8") as handle:
        if path.suffix in {".yml", ".yaml"}:
            if yaml is None:
                raise RuntimeError(
                    "PyYAML is required to load YAML configuration files. Install it or use JSON."
                )
            data = yaml.safe_load(handle) or {}
        elif path.suffix == ".json":
            data = json.load(handle)
        else:
            raise ValueError(f"Unsupported config extension: {path.suffix}")

    config = AppConfig.from_dict(data)
    _ensure_directories(config)
    return config


def _ensure_directories(config: AppConfig) -> None:
    """Create directories required for the agent to operate."""

    config.pipeline.workspace.mkdir(parents=True, exist_ok=True)
    config.renderer.image_dir = config.pipeline.workspace / config.renderer.image_dir
    config.renderer.image_dir.mkdir(parents=True, exist_ok=True)


__all__ = [
    "FreeCADConfig",
    "LLMConfig",
    "RendererConfig",
    "PipelineConfig",
    "AppConfig",
    "load_config",
]
