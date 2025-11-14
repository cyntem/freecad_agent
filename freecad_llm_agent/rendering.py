"""Rendering helpers that save placeholder images for generated geometry."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

# Pillow is optional: use a lightweight fallback if it is not available.
try:  # pragma: no cover - optional dependency
    from PIL import Image, ImageDraw, ImageFont  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    Image = ImageDraw = ImageFont = None  # type: ignore

from .config import RendererConfig

logger = logging.getLogger(__name__)


def _load_font(size: int = 16):  # type: ignore[override]
    if ImageFont is None:
        return None  # type: ignore[return-value]
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:  # pragma: no cover - environment dependent
        return ImageFont.load_default()


@dataclass
class RenderResult:
    view: str
    image_path: Path


class Renderer:
    """Generate placeholder renderings for reporting and debugging."""

    def __init__(self, config: RendererConfig) -> None:
        self._config = config
        self._font = _load_font() if ImageFont else None

    def render(self, requirement: str, iteration: int) -> List[RenderResult]:
        self._config.image_dir.mkdir(parents=True, exist_ok=True)
        results: List[RenderResult] = []
        for view in self._config.views:
            path = self._config.image_dir / f"{iteration:02d}_{view}.png"
            self._draw_placeholder(requirement, view, path)
            results.append(RenderResult(view=view, image_path=path))
            logger.info("Rendered %s view to %s", view, path)
        return results

    def _draw_placeholder(self, requirement: str, view: str, output_path: Path) -> None:
        text = f"Requirement: {requirement[:60]}...\nView: {view}"
        if Image and ImageDraw and ImageFont:
            image = Image.new("RGB", (self._config.width, self._config.height), color=(8, 20, 40))
            draw = ImageDraw.Draw(image)
            draw.text((20, 20), text, font=self._font or ImageFont.load_default(), fill=(240, 240, 240))
            image.save(output_path)
        else:
            output_path.write_text(text, encoding="utf-8")


__all__ = ["Renderer", "RenderResult"]
