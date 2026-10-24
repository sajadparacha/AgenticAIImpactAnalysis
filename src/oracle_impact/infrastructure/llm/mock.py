"""Scripted model used by tests and local dry runs."""

from oracle_impact.infrastructure.llm.provider import ModelResponse


class ScriptedProvider:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def complete(self, messages: list[dict], tools: list[dict]) -> ModelResponse:
        self.calls += 1
        if not self._responses:
            raise RuntimeError("The scripted model has no remaining response.")
        return self._responses.pop(0)
