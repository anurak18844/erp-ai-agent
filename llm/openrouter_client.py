from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel

from config.settings import Settings, get_settings


T = TypeVar("T", bound=BaseModel)


class OpenRouterError(RuntimeError):
    """A sanitized OpenRouter API failure suitable for application logs."""


class OpenRouterOutputLimitError(OpenRouterError):
    """The provider spent the completion budget before producing final content."""


class LLMClient(Protocol):
    async def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.1) -> str: ...
    async def generate_structured(
        self, messages: list[dict[str, str]], response_model: type[T], *, temperature: float = 0.0
    ) -> T: ...


class OpenRouterClient:
    def __init__(self, settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings or get_settings()
        self.transport = transport

    def _headers(self) -> dict[str, str]:
        if not self.settings.openrouter_api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")
        return {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost/erp-ai-agent",
            "X-Title": "ERP AI Agent Prototype",
        }

    async def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self.settings.openrouter_base_url,
            headers=self._headers(),
            timeout=httpx.Timeout(60.0, connect=10.0),
            transport=self.transport,
        ) as client:
            for attempt in range(2):
                try:
                    response = await client.post("/chat/completions", json=payload)
                except httpx.RequestError as exc:
                    if attempt == 0:
                        await asyncio.sleep(0.25)
                        continue
                    raise OpenRouterError(f"OpenRouter request failed: {type(exc).__name__}") from exc

                try:
                    body = response.json()
                except ValueError as exc:
                    if attempt == 0 and response.is_success:
                        await asyncio.sleep(0.25)
                        continue
                    excerpt = response.text[:500].replace("\r", " ").replace("\n", " ")
                    raise OpenRouterError(
                        f"OpenRouter HTTP {response.status_code} returned invalid JSON: {excerpt!r}"
                    ) from exc

                retryable_status = response.status_code in {408, 409, 429} or response.status_code >= 500
                if not response.is_success:
                    if attempt == 0 and retryable_status:
                        await asyncio.sleep(0.25)
                        continue
                    raise OpenRouterError(
                        f"OpenRouter HTTP {response.status_code}: {self._body_excerpt(body)}"
                    )

                # Some providers return an error envelope with HTTP 200. Treat it as
                # a failed completion instead of allowing a misleading KeyError on choices.
                if not isinstance(body, dict):
                    if attempt == 0:
                        await asyncio.sleep(0.25)
                        continue
                    raise OpenRouterError(
                        f"OpenRouter HTTP 200 returned {type(body).__name__}, expected an object"
                    )
                if body.get("error"):
                    error = body["error"]
                    code = error.get("code") if isinstance(error, dict) else None
                    retryable_error = code in {408, 409, 429} or (
                        isinstance(code, int) and code >= 500
                    )
                    if attempt == 0 and retryable_error:
                        await asyncio.sleep(0.25)
                        continue
                    raise OpenRouterError(
                        f"OpenRouter HTTP 200 error envelope: {self._body_excerpt(body)}"
                    )
                choices = body.get("choices")
                if not isinstance(choices, list) or not choices:
                    if attempt == 0:
                        await asyncio.sleep(0.25)
                        continue
                    raise OpenRouterError(
                        "OpenRouter HTTP 200 response did not contain a non-empty choices list: "
                        f"{self._body_excerpt(body)}"
                    )
                return body

        raise OpenRouterError("OpenRouter request failed without a response")

    @staticmethod
    def _body_excerpt(body: Any) -> str:
        try:
            return json.dumps(body, ensure_ascii=False, default=str)[:2000]
        except (TypeError, ValueError):
            return repr(body)[:2000]

    @staticmethod
    def _message_content(body: dict[str, Any]) -> Any:
        choice = body["choices"][0]
        if not isinstance(choice, dict):
            raise OpenRouterError("OpenRouter choice was not an object")
        message = choice.get("message")
        if not isinstance(message, dict) or "content" not in message:
            raise OpenRouterError(
                "OpenRouter choice did not contain message.content: "
                f"{OpenRouterClient._body_excerpt(choice)}"
            )
        content = message["content"]
        if content is None:
            finish_reason = choice.get("finish_reason") or choice.get("native_finish_reason")
            reasoning = message.get("reasoning") or message.get("reasoning_content") or ""
            detail = (
                f"finish_reason={finish_reason!r}, "
                f"reasoning_chars={len(reasoning) if isinstance(reasoning, str) else 'present'}"
            )
            if finish_reason in {"length", "max_tokens"}:
                raise OpenRouterOutputLimitError(
                    "OpenRouter exhausted the output-token budget before producing "
                    f"message.content ({detail})"
                )
            raise OpenRouterError(f"OpenRouter returned null message.content ({detail})")
        return content

    async def _completion_content(self, payload: dict[str, Any]) -> Any:
        current = dict(payload)
        for attempt in range(2):
            body = await self._request(current)
            try:
                return self._message_content(body)
            except OpenRouterOutputLimitError:
                current_limit = int(current.get("max_tokens") or 0)
                expanded_limit = min(max(current_limit * 4, 8192), 32768)
                if attempt == 0 and expanded_limit > current_limit:
                    current["max_tokens"] = expanded_limit
                    continue
                raise
        raise OpenRouterError("OpenRouter completion failed without content")

    async def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.1) -> str:
        content = await self._completion_content({
            "model": self.settings.openrouter_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self.settings.openrouter_max_output_tokens,
            "provider": {"sort": "latency"},
        })
        return str(content)

    async def generate_structured(
        self, messages: list[dict[str, str]], response_model: type[T], *, temperature: float = 0.0
    ) -> T:
        schema = response_model.model_json_schema()
        content = await self._completion_content({
            "model": self.settings.openrouter_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self.settings.openrouter_max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": response_model.__name__.lower(),
                    "strict": True,
                    "schema": schema,
                },
            },
            "provider": {"require_parameters": True, "sort": "latency"},
        })
        parsed = self._parse_structured_content(content)
        return response_model.model_validate(parsed)

    @staticmethod
    def _parse_structured_content(content: Any) -> Any:
        if not isinstance(content, str):
            return content
        candidate = content.strip()
        if candidate.startswith("```json") and candidate.endswith("```"):
            candidate = candidate[7:-3].strip()
        elif candidate.startswith("```") and candidate.endswith("```"):
            candidate = candidate[3:-3].strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            start = max(0, exc.pos - 120)
            end = min(len(candidate), exc.pos + 120)
            excerpt = candidate[start:end].replace("\r", " ").replace("\n", " ")
            raise OpenRouterError(
                "Model returned invalid structured JSON "
                f"(line {exc.lineno}, column {exc.colno}, response_chars={len(candidate)}): {excerpt!r}"
            ) from exc
