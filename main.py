"""Command line interface for running the FreeCAD LLM agent."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from freecad_llm_agent.config import AppConfig, load_config
from freecad_llm_agent.pipeline import DesignAgent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LLM-powered FreeCAD automation agent")
    parser.add_argument("requirement", help="Path to a text file with the design requirement or the requirement itself")
    parser.add_argument("--config", type=Path, default=None, help="Path to the YAML/JSON configuration file")
    parser.add_argument("--is-file", action="store_true", help="Interpret requirement argument as a file path")
    return parser.parse_args()


def load_requirement(arg: str, is_file: bool) -> str:
    path = Path(arg)
    if is_file or path.exists():
        return path.read_text(encoding="utf-8")
    return arg


def main() -> None:
    args = parse_args()
    requirement = load_requirement(args.requirement, args.is_file)
    config = load_config(args.config)
    agent = DesignAgent(config)
    report = agent.run(requirement)

    summary: dict[str, Any] = {
        "success": report.successful,
        "artifacts": [
            {
                "iteration": artifact.iteration,
                "script": str(artifact.script_path),
                "renders": [str(path) for path in artifact.render_paths],
                "success": artifact.success,
                "error": artifact.error,
            }
            for artifact in report.artifacts
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    main()
