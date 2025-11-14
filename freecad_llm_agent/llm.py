"""LLM client abstractions used by the agent."""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Dict, Any

try:  # pragma: no cover - optional dependency for remote providers
    import httpx
except ImportError:  # pragma: no cover - optional dependency for remote providers
    httpx = None  # type: ignore

logger = logging.getLogger(__name__)


@dataclass
class Message:
    """Single chat message."""

    role: str
    content: str


@dataclass
class OpenRouterModelInfo:
    """Description of a model exposed via OpenRouter."""

    model_id: str
    vendor: str
    display_name: str
    supports_images: Optional[bool] = None


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
        lowered = prompt.lower()
        if "=== render review ===" in lowered:
            return json.dumps(
                {
                    "needs_additional_views": False,
                    "feedback": "Rendered projections inspected in dummy mode.",
                }
            )
        if "assembly" in lowered or "сборк" in lowered:
            return self._assembly_template(prompt)
        if "error" in lowered:
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


class _BaseHTTPChatClient(LLMClient):
    """Shared utilities for HTTP-based chat completion providers."""

    def __init__(
        self,
        base_url: str,
        headers: dict[str, str],
        model: str,
        max_tokens: int,
        temperature: float,
        timeout: float = 60.0,
        path: str = "/chat/completions",
        extra_params: Optional[dict[str, str]] = None,
    ) -> None:
        if httpx is None:  # pragma: no cover - runtime guard
            raise RuntimeError("httpx is required for HTTP-based LLM providers. Install it via pip.")
        self._client = httpx.Client(base_url=base_url, headers=headers, timeout=timeout)
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._path = path
        self._extra_params = extra_params or {}

    def complete(self, messages: Sequence[Message], images: Optional[Iterable[str]] = None) -> str:
        payload = {
            "model": self._model,
            "messages": _messages_with_images(messages, images),
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
        }
        payload.update(self._extra_params)
        response = self._client.post(self._path, json=payload)
        response.raise_for_status()
        data = response.json()
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError) as error:  # pragma: no cover - defensive
            raise RuntimeError(f"Malformed LLM response: {data}") from error


class OpenAILLMClient(_BaseHTTPChatClient):
    """LLM client that targets the public OpenAI REST API."""

    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int,
        temperature: float,
        api_base: Optional[str] = None,
        organization: Optional[str] = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {api_key}"}
        if organization:
            headers["OpenAI-Organization"] = organization
        super().__init__(
            base_url=api_base or "https://api.openai.com/v1",
            headers=headers,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )


class AzureOpenAILLMClient(_BaseHTTPChatClient):
    """LLM client configured for Azure OpenAI deployments."""

    def __init__(
        self,
        api_key: str,
        endpoint: str,
        deployment: str,
        api_version: str,
        max_tokens: int,
        temperature: float,
    ) -> None:
        if not endpoint.endswith("/"):
            endpoint = endpoint + "/"
        path = f"openai/deployments/{deployment}/chat/completions?api-version={api_version}"
        headers = {"api-key": api_key}
        super().__init__(
            base_url=endpoint,
            headers=headers,
            model=deployment,
            max_tokens=max_tokens,
            temperature=temperature,
            path=path,
        )


class LocalLLMClient(_BaseHTTPChatClient):
    """Simple HTTP client that targets locally hosted models with an OpenAI-compatible API."""

    def __init__(
        self,
        endpoint: str,
        model: str,
        max_tokens: int,
        temperature: float,
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        if not endpoint:
            raise ValueError("Local endpoint URL must be provided for the 'local' provider")
        super().__init__(
            base_url=endpoint,
            headers=headers or {},
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )


class OpenRouterLLMClient(_BaseHTTPChatClient):
    """Client for https://openrouter.ai REST API."""

    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int,
        temperature: float,
        api_base: Optional[str] = None,
        site_url: Optional[str] = None,
        app_name: Optional[str] = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {api_key}"}
        if site_url:
            headers["HTTP-Referer"] = site_url
        if app_name:
            headers["X-Title"] = app_name
        super().__init__(
            base_url=api_base or "https://openrouter.ai/api/v1",
            headers=headers,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )


def _messages_with_images(
    messages: Sequence[Message], images: Optional[Iterable[str]] = None
) -> List[dict]:
    payload: List[dict] = []
    for message in messages:
        payload.append({"role": message.role, "content": [{"type": "text", "text": message.content}]})

    if images:
        image_payload = _encode_images(images)
        if not payload or payload[-1]["role"] != "user":
            payload.append({"role": "user", "content": []})
        payload[-1]["content"].extend(image_payload)
    return payload


def _encode_images(images: Iterable[str]) -> List[dict]:
    encoded: List[dict] = []
    for path in images:
        image_path = Path(path)
        if not image_path.exists():
            logger.warning("Render image %s is missing, skipping", image_path)
            continue
        data = base64.b64encode(image_path.read_bytes()).decode("ascii")
        mime, _ = mimetypes.guess_type(image_path.name)
        mime = mime or "image/png"
        encoded.append({"type": "input_image", "image_url": {"url": f"data:{mime};base64,{data}"}})
    return encoded


def create_llm_client(config: "LLMConfig") -> LLMClient:
    """Return an ``LLMClient`` based on the provided configuration."""

    provider = config.provider.lower()
    if provider == "openai":
        if not config.api_key:
            raise RuntimeError("OpenAI provider requires api_key")
        return OpenAILLMClient(
            api_key=config.api_key,
            model=config.model,
            api_base=config.api_base,
            organization=config.organization,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
        )
    if provider == "azure":
        if not (config.api_key and config.azure_endpoint and config.azure_deployment):
            raise RuntimeError("Azure provider requires api_key, azure_endpoint and azure_deployment")
        return AzureOpenAILLMClient(
            api_key=config.api_key,
            endpoint=config.azure_endpoint,
            deployment=config.azure_deployment,
            api_version=config.azure_api_version,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
        )
    if provider == "local":
        if not config.local_endpoint:
            raise RuntimeError("Local provider requires local_endpoint")
        return LocalLLMClient(
            endpoint=config.local_endpoint,
            model=config.model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            headers=config.local_headers,
        )
    if provider == "openrouter":
        if not config.api_key:
            raise RuntimeError("OpenRouter provider requires api_key")
        return OpenRouterLLMClient(
            api_key=config.api_key,
            model=config.model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            api_base=config.openrouter_api_base,
            site_url=config.openrouter_site_url,
            app_name=config.openrouter_app_name,
        )

    return DummyLLMClient(model=config.model, temperature=config.temperature)


def fetch_openrouter_models(
    api_key: str, api_base: Optional[str] = None, timeout: float = 30.0
) -> List[OpenRouterModelInfo]:
    """Return metadata about models that are available to the OpenRouter account."""

    if httpx is None:  # pragma: no cover - runtime guard
        raise RuntimeError("httpx is required to query the OpenRouter API")
    base_url = api_base or "https://openrouter.ai/api/v1"
    url = f"{base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    response = httpx.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    models: List[OpenRouterModelInfo] = []
    for item in data.get("data", []):
        model_id = item.get("id")
        if not model_id:
            continue
        vendor = _extract_vendor(str(model_id), item)
        display_name = _extract_display_name(str(model_id), item)
        supports_images = _detect_image_support(item.get("architecture"))
        models.append(
            OpenRouterModelInfo(
                model_id=str(model_id),
                vendor=vendor,
                display_name=display_name,
                supports_images=supports_images,
            )
        )
    models.sort(key=lambda info: (info.vendor.lower(), info.display_name.lower()))
    return models


def _extract_vendor(model_id: str, payload: Dict[str, Any]) -> str:
    if "/" in model_id:
        return model_id.split("/", 1)[0]
    name = payload.get("name")
    if isinstance(name, str) and ":" in name:
        return name.split(":", 1)[0].strip().lower().replace(" ", "-")
    return "unknown"


def _extract_display_name(model_id: str, payload: Dict[str, Any]) -> str:
    name = payload.get("name")
    if isinstance(name, str) and name.strip():
        return name.split(":", 1)[-1].strip() if ":" in name else name.strip()
    return model_id


def _detect_image_support(architecture: Optional[Dict[str, Any]]) -> Optional[bool]:
    if not architecture:
        return None
    modalities = architecture.get("input_modalities")
    if isinstance(modalities, list):
        normalized = " ".join(str(mod).lower() for mod in modalities)
        if "image" in normalized:
            return True
        if normalized:
            return False
    modality = architecture.get("modality")
    if isinstance(modality, str) and modality:
        return "image" in modality.lower()
    return None


def dump_messages(messages: Sequence[Message]) -> str:
    """Return a human-readable representation of the conversation."""

    return json.dumps([message.__dict__ for message in messages], ensure_ascii=False, indent=2)


__all__ = [
    "Message",
    "LLMClient",
    "DummyLLMClient",
    "OpenAILLMClient",
    "AzureOpenAILLMClient",
    "LocalLLMClient",
    "OpenRouterLLMClient",
    "create_llm_client",
    "fetch_openrouter_models",
    "OpenRouterModelInfo",
    "dump_messages",
]
