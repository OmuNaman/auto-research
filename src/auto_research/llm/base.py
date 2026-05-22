"""LLMProvider abstraction.

`stream_phase` runs a single tool-use loop ("one phase") to completion. The
state machine outside owns the higher-level transitions. The provider invokes
`on_event` for every meaningful streaming event so the planner can translate
into domain events on the EventBus.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, Union, runtime_checkable


@runtime_checkable
class ToolHandler(Protocol):
    async def __call__(self, args: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ToolDef:
    """Provider-agnostic tool description.

    `input_schema` is a JSON-schema dict (object). `handler` is an async callable
    `(args: dict) -> dict`. The Anthropic adapter wraps this into the SDK's
    `@tool` decorator at registration time.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler


# ---------------------------------------------------------------- stream events


@dataclass(frozen=True)
class LLMTextDelta:
    type: Literal["text_delta"] = "text_delta"
    agent: str = "planner"
    message_id: str = ""
    text: str = ""


@dataclass(frozen=True)
class LLMThinkingDelta:
    type: Literal["thinking_delta"] = "thinking_delta"
    agent: str = "planner"
    message_id: str = ""
    text: str = ""


@dataclass(frozen=True)
class LLMToolCall:
    type: Literal["tool_call"] = "tool_call"
    agent: str = "planner"
    tool: str = ""
    tool_call_id: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMToolResult:
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str = ""
    tool: str = ""
    ok: bool = True
    output: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: int = 0


@dataclass(frozen=True)
class LLMTurnComplete:
    type: Literal["turn_complete"] = "turn_complete"
    agent: str = "planner"
    final_text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_cost_usd: float = 0.0


LLMStreamEvent = Union[
    LLMTextDelta, LLMThinkingDelta, LLMToolCall, LLMToolResult, LLMTurnComplete
]


OnEvent = Callable[[LLMStreamEvent], Awaitable[None]]


@dataclass(frozen=True)
class PhaseResult:
    final_text: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    total_cost_usd: float
    raw_messages: list[Any] = field(default_factory=list)


# ----------------------------------------------------------------- subagent def


@dataclass(frozen=True)
class SubAgentSpec:
    """Our wire-level spec for an SDK sub-agent.

    Adapter converts to claude_agent_sdk.AgentDefinition at registration.
    """

    name: str
    description: str
    prompt: str
    tools: list[str] | None = None
    model: str | None = None


# ----------------------------------------------------------------- provider ABC


class LLMProvider(ABC):
    @abstractmethod
    async def stream_phase(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        tools: list[ToolDef],
        subagents: list[SubAgentSpec] | None = None,
        on_event: OnEvent,
        agent_name: str = "planner",
        max_turns: int | None = None,
        builtin_tools: list[str] | None = None,
    ) -> PhaseResult:
        """Run one tool-use loop to completion, emitting events via on_event.

        ``builtin_tools`` lists Claude Code built-in tool names to enable for
        this phase (e.g. ``["WebSearch", "WebFetch"]``). They are added to the
        allowed-tools list alongside any MCP tools in ``tools``.
        """
