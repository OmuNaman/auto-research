"""Anthropic Claude provider — thin adapter over claude_agent_sdk.

Boundary: the SDK owns the inner tool-use loop for one phase; we translate its
message stream into our `LLMStreamEvent` protocol.

Mock note: tests don't import this module at all — they substitute their own
`LLMProvider`. So this module is allowed to fail at import time on machines
without the `claude` CLI installed.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    tool,
)

from auto_research.llm.base import (
    LLMProvider,
    LLMTextDelta,
    LLMThinkingDelta,
    LLMToolCall,
    LLMToolResult,
    LLMTurnComplete,
    OnEvent,
    PhaseResult,
    SubAgentSpec,
    ToolDef,
)
from auto_research.logging import get_logger

_log = get_logger(__name__)

MCP_SERVER_NAME = "auto_research_tools"


def _wrap_tool(td: ToolDef):
    """Adapt our ToolDef into the SDK's @tool decorator form."""

    @tool(td.name, td.description, td.input_schema)
    async def _impl(args: dict[str, Any]) -> dict[str, Any]:
        try:
            out = await td.handler(args)
        except Exception as exc:  # noqa: BLE001
            return {
                "content": [{"type": "text", "text": f"ERROR: {exc!s}"}],
                "isError": True,
            }
        # The SDK expects MCP tool result shape: {"content": [{type:text, text:...}]}.
        # We pass-through arbitrary dicts as JSON-stringified text.
        import json
        return {"content": [{"type": "text", "text": json.dumps(out, default=str)}]}

    return _impl


def _to_agent_definition(spec: SubAgentSpec) -> AgentDefinition:
    return AgentDefinition(
        description=spec.description,
        prompt=spec.prompt,
        tools=spec.tools,
        model=spec.model,
    )


class AnthropicProvider(LLMProvider):
    def __init__(self, *, model: str = "claude-opus-4-7", cwd: str | None = None) -> None:
        self._model = model
        self._cwd = cwd

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
        sdk_tools = [_wrap_tool(t) for t in tools]
        server = create_sdk_mcp_server(name=MCP_SERVER_NAME, version="0.1.0", tools=sdk_tools)

        # MCP tool names + any requested Claude Code built-in tool names
        allowed = [f"mcp__{MCP_SERVER_NAME}__{t.name}" for t in tools]
        if builtin_tools:
            allowed = allowed + builtin_tools
        agents = (
            {s.name: _to_agent_definition(s) for s in subagents} if subagents else None
        )

        options = ClaudeAgentOptions(
            model=self._model,
            system_prompt=system_prompt,
            mcp_servers={MCP_SERVER_NAME: server},
            allowed_tools=allowed,
            tools=builtin_tools or None,
            permission_mode="bypassPermissions",
            agents=agents,
            cwd=self._cwd,
            max_turns=max_turns,
            include_partial_messages=True,
        )

        message_ids: dict[int, str] = {}
        tool_call_starts: dict[str, float] = {}
        final_text_parts: list[str] = []
        raw: list[Any] = []
        input_tokens = output_tokens = cache_read = cache_write = 0
        total_cost = 0.0

        async with ClaudeSDKClient(options=options) as client:
            await client.query(user_prompt)
            msg_idx = 0
            async for msg in client.receive_response():
                raw.append(msg)
                msg_idx += 1

                if isinstance(msg, AssistantMessage):
                    msg_id = message_ids.setdefault(msg_idx, uuid.uuid4().hex)
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            final_text_parts.append(block.text)
                            await on_event(LLMTextDelta(
                                agent=agent_name, message_id=msg_id, text=block.text,
                            ))
                        elif isinstance(block, ThinkingBlock):
                            await on_event(LLMThinkingDelta(
                                agent=agent_name, message_id=msg_id, text=block.thinking,
                            ))
                        elif isinstance(block, ToolUseBlock):
                            tool_call_starts[block.id] = time.time()
                            await on_event(LLMToolCall(
                                agent=agent_name,
                                tool=block.name,
                                tool_call_id=block.id,
                                input=dict(block.input) if block.input else {},
                            ))

                elif isinstance(msg, UserMessage):
                    # tool results arrive as UserMessage(content=[ToolResultBlock(...)])
                    for block in msg.content:
                        if isinstance(block, ToolResultBlock):
                            started = tool_call_starts.pop(block.tool_use_id, time.time())
                            duration_ms = int((time.time() - started) * 1000)
                            err = block.is_error or False
                            payload: dict[str, Any] | None = None
                            err_text: str | None = None
                            if isinstance(block.content, list):
                                txt_parts = []
                                for c in block.content:
                                    if isinstance(c, dict) and c.get("type") == "text":
                                        txt_parts.append(c.get("text", ""))
                                payload = {"text": "".join(txt_parts)}
                                if err:
                                    err_text = payload["text"]
                            elif isinstance(block.content, str):
                                payload = {"text": block.content}
                                if err:
                                    err_text = block.content
                            await on_event(LLMToolResult(
                                tool_call_id=block.tool_use_id,
                                tool="",
                                ok=not err,
                                output=payload if not err else None,
                                error=err_text,
                                duration_ms=duration_ms,
                            ))

                elif isinstance(msg, ResultMessage):
                    usage = getattr(msg, "usage", None) or {}
                    input_tokens = int(usage.get("input_tokens", 0) or 0)
                    output_tokens = int(usage.get("output_tokens", 0) or 0)
                    cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
                    cache_write = int(usage.get("cache_creation_input_tokens", 0) or 0)
                    total_cost = float(getattr(msg, "total_cost_usd", 0.0) or 0.0)

                elif isinstance(msg, SystemMessage):
                    pass

        final_text = "".join(final_text_parts)
        await on_event(LLMTurnComplete(
            agent=agent_name,
            final_text=final_text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            total_cost_usd=total_cost,
        ))
        return PhaseResult(
            final_text=final_text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            total_cost_usd=total_cost,
            raw_messages=raw,
        )
