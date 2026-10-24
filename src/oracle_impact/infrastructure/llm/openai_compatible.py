"""OpenAI-compatible tool-calling client.

Egress must be enabled by configuration. This module is inactive unless the
investigator is constructed with an approved base URL. Requests are not logged.
"""

import json

import httpx

from oracle_impact.infrastructure.llm.provider import ModelResponse, ToolCall


class EgressDisabled(RuntimeError):
    """Raised when a live model call is attempted while egress is off."""


class OpenAICompatibleProvider:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float, egress_enabled: bool) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._egress_enabled = egress_enabled

    @property
    def model(self) -> str:
        return self._model

    def with_model(self, model: str) -> "OpenAICompatibleProvider":
        return OpenAICompatibleProvider(self._base_url, self._api_key, model, self._timeout, self._egress_enabled)

    def list_models(self, timeout: float = 3.0) -> list[str]:
        """Model ids served by the endpoint. Sends no source or prompt."""
        if not self._egress_enabled:
            raise EgressDisabled("LLM egress is disabled.")
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        response = httpx.get(f"{self._base_url}/models", headers=headers, timeout=timeout)
        response.raise_for_status()
        entries = response.json().get("data") or []
        return sorted({str(item.get("id")) for item in entries if isinstance(item, dict) and item.get("id")})

    def complete(self, messages: list[dict], tools: list[dict]) -> ModelResponse:
        if not self._egress_enabled:
            raise EgressDisabled("LLM egress is disabled. No source was sent.")
        payload = {
            "model": self._model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
        }
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        response = httpx.post(
            f"{self._base_url}/chat/completions",
            json=payload,
            headers=headers,
            timeout=self._timeout,
        )
        response.raise_for_status()
        body = response.json()
        choice = body["choices"][0]["message"]
        usage = body.get("usage") or {}
        tool_calls = []
        for call in choice.get("tool_calls") or []:
            raw_arguments = call.get("function", {}).get("arguments") or "{}"
            arguments = _arguments(raw_arguments)
            tool_calls.append(
                ToolCall(
                    name=call.get("function", {}).get("name", ""),
                    arguments=arguments,
                    call_id=call.get("id", ""),
                )
            )
        return ModelResponse(
            content=choice.get("content"),
            reasoning=_reasoning_text(choice),
            tool_calls=tool_calls,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )


def _reasoning_text(choice: dict) -> str:
    raw = choice.get("reasoning_content") or choice.get("reasoning") or ""
    if isinstance(raw, dict):
        raw = raw.get("content") or ""
    return raw if isinstance(raw, str) else ""


def _arguments(raw_arguments: object) -> dict:
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if not isinstance(raw_arguments, str) or not raw_arguments.strip():
        return {}
    try:
        parsed = json.loads(raw_arguments)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
