from __future__ import annotations

import httpx

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

    monkeypatch.setattr(llm_module.httpx, "Client", DummyClient)

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

    monkeypatch.setattr(llm_module.httpx, "get", fake_get)

    models = fetch_openrouter_models("token")
    assert len(models) == 2
    assert models[0].model_id == "model-a"
    assert models[0].vendor == "unknown"
    assert models[0].supports_images is None


def test_openrouter_client_retries_on_rate_limit(monkeypatch):
    attempts = {"count": 0}
    sleeps: list[float] = []

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def post(self, path: str, json: dict) -> object:
            attempts["count"] += 1
            if attempts["count"] == 1:
                request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
                response = httpx.Response(429, request=request, headers={"Retry-After": "0.1"})

                class ErrorResponse:
                    headers = response.headers

                    def raise_for_status(self) -> None:
                        raise httpx.HTTPStatusError("429", request=request, response=response)

                    def json(self) -> dict:
                        return {}

                return ErrorResponse()

            class OkResponse:
                headers: dict[str, str] = {}

                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict:
                    return {"choices": [{"message": {"content": "second attempt"}}]}

            return OkResponse()

    monkeypatch.setattr(llm_module.httpx, "Client", DummyClient)
    monkeypatch.setattr(llm_module.time, "sleep", lambda delay: sleeps.append(delay))

    config = LLMConfig(provider="openrouter", api_key="token", model="openrouter/model", max_tokens=64, temperature=0.1)
    client = create_llm_client(config)
    result = client.complete([Message(role="user", content="hello")])

    assert result == "second attempt"
    assert attempts["count"] == 2
    assert sleeps and sleeps[0] >= 0.1
