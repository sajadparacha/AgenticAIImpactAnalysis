"""LLM provider contracts. Implementations must not accept model-authored SQL."""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ToolCall:
    name: str
    arguments: dict
    call_id: str = ""


@dataclass
class ModelResponse:
    content: str | None = None
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LlmProvider(Protocol):
    def complete(self, messages: list[dict], tools: list[dict]) -> ModelResponse:
        """Return the next model message. Raise on transport or budget failures."""


class BudgetExceeded(RuntimeError):
    """The investigator stopped because a configured limit was reached."""
