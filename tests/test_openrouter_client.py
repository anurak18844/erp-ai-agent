import json

import httpx
import pytest

from config.settings import Settings
from llm.openrouter_client import (
    OpenRouterClient,
    OpenRouterError,
    OpenRouterOutputLimitError,
)
from models.intent import IntentAnalysis


@pytest.mark.asyncio
async def test_invalid_structured_json_is_sanitized(settings: Settings):
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["max_tokens"] == 4096
        assert "reasoning" not in payload
        assert payload["provider"]["sort"] == "latency"
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"intent": "x", broken}'}}]
        })

    test_settings = settings.model_copy(update={"openrouter_api_key": "test-key"})
    client = OpenRouterClient(test_settings, transport=httpx.MockTransport(handler))
    with pytest.raises(OpenRouterError, match="invalid structured JSON") as raised:
        await client.generate_structured([], IntentAnalysis)
    assert "response_chars=" in str(raised.value)


@pytest.mark.asyncio
async def test_http_200_error_envelope_raises_openrouter_error(settings: Settings):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "error": {"message": "Provider rejected completion", "code": 400}
        })

    test_settings = settings.model_copy(update={"openrouter_api_key": "test-key"})
    client = OpenRouterClient(test_settings, transport=httpx.MockTransport(handler))
    with pytest.raises(OpenRouterError, match="HTTP 200 error envelope") as raised:
        await client.generate_structured([], IntentAnalysis)
    assert "Provider rejected completion" in str(raised.value)


@pytest.mark.asyncio
async def test_http_200_missing_choices_is_retried_once(settings: Settings):
    requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            return httpx.Response(200, json={"id": "transient-response"})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps({
                "intent": "query",
                "primary_domain": "rental",
                "secondary_domains": [],
                "needs_database": True,
            })}}]
        })

    test_settings = settings.model_copy(update={"openrouter_api_key": "test-key"})
    client = OpenRouterClient(test_settings, transport=httpx.MockTransport(handler))
    result = await client.generate_structured([], IntentAnalysis)
    assert result.intent == "query"
    assert requests == 2


@pytest.mark.asyncio
async def test_http_200_missing_choices_after_retry_is_descriptive(settings: Settings):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "no-choices", "model": "test-model"})

    test_settings = settings.model_copy(update={"openrouter_api_key": "test-key"})
    client = OpenRouterClient(test_settings, transport=httpx.MockTransport(handler))
    with pytest.raises(OpenRouterError, match="non-empty choices list") as raised:
        await client.generate_structured([], IntentAnalysis)
    assert "no-choices" in str(raised.value)


@pytest.mark.asyncio
async def test_length_finish_retries_with_larger_output_budget(settings: Settings):
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return httpx.Response(200, json={
                "choices": [{
                    "finish_reason": "length",
                    "message": {"content": None, "reasoning": "private reasoning"},
                }]
            })
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps({
                "intent": "query",
                "primary_domain": "rental",
                "secondary_domains": [],
                "needs_database": True,
            })}}]
        })

    test_settings = settings.model_copy(update={"openrouter_api_key": "test-key"})
    client = OpenRouterClient(test_settings, transport=httpx.MockTransport(handler))
    result = await client.generate_structured([], IntentAnalysis)
    assert result.intent == "query"
    assert requests[0]["max_tokens"] == 4096
    assert requests[1]["max_tokens"] == 16384


@pytest.mark.asyncio
async def test_length_error_does_not_expose_reasoning(settings: Settings):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{
                "finish_reason": "length",
                "message": {"content": None, "reasoning": "do not log this secret thought"},
            }]
        })

    test_settings = settings.model_copy(update={
        "openrouter_api_key": "test-key",
        "openrouter_max_output_tokens": 32768,
    })
    client = OpenRouterClient(test_settings, transport=httpx.MockTransport(handler))
    with pytest.raises(OpenRouterOutputLimitError, match="output-token budget") as raised:
        await client.generate_structured([], IntentAnalysis)
    assert "do not log" not in str(raised.value)
    assert "reasoning_chars=" in str(raised.value)
