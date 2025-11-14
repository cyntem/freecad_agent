from __future__ import annotations

import types

from freecad_llm_agent import llm as llm_module
from freecad_llm_agent.config import LLMConfig
from freecad_llm_agent.llm import Message, OpenRouterLLMClient, create_llm_client, fetch_openrouter_models


def test_create_llm_client_openrouter(monkeypatch):
    captured: dict[str, object] = {}

    class DummyClient:
        def __init__(self, base_url: str, headers: dict[str, str], timeout: float) -> None:
            captured["base_url"] = base_url
            captured["headers"] = headers
            captured["timeout"] = timeout

        def post(self, path: str, json: dict) -> object:
            class Response:
                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict:
                    return {"choices": [{"message": {"content": "ok"}}]}

            captured["payload"] = json
            return Response()

    fake_httpx = types.SimpleNamespace(Client=DummyClient, get=lambda *args, **kwargs: None)
    monkeypatch.setattr(llm_module, "httpx", fake_httpx)

    config = LLMConfig(provider="openrouter", api_key="token", model="openrouter/model", max_tokens=128, temperature=0.3)
    client = create_llm_client(config)
    client.complete([Message(role="user", content="test")])

    assert isinstance(client, OpenRouterLLMClient)
    assert captured["base_url"] == "https://openrouter.ai/api/v1"
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == "Bearer token"
    assert captured["payload"]["model"] == "openrouter/model"


def test_fetch_openrouter_models(monkeypatch):
    class DummyResponse:
        def __init__(self) -> None:
            self._data = {"data": [{"id": "model-a"}, {"id": "model-b"}]}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._data

    def fake_get(url: str, headers: dict, timeout: float) -> DummyResponse:
        assert url == "https://openrouter.ai/api/v1/models"
        assert headers["Authorization"] == "Bearer token"
        assert timeout == 30.0
        return DummyResponse()

    fake_httpx = types.SimpleNamespace(get=fake_get)
    monkeypatch.setattr(llm_module, "httpx", fake_httpx)

    models = fetch_openrouter_models("token")
    assert models == ["model-a", "model-b"]
