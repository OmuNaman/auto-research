"""Tests for the LLMProvider abstraction itself (no real Claude calls).

We exercise the protocol with a FakeProvider that emits a canned sequence of
LLMStreamEvents, and assert the contract: on_event is awaited in order,
PhaseResult aggregates usage correctly, and our planner-side translation logic
into domain events behaves.
"""

import asyncio
from collections.abc import Awaitable, Callable

from auto_research.events.bus import SQLiteEventBus
from auto_research.events.schemas import (
    LLMMessageDeltaEvent,
    LLMToolCallEvent,
    ToolResultEvent,
)
from auto_research.llm.base import (
    LLMProvider,
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCall,
    LLMToolResult,
    LLMTurnComplete,
    PhaseResult,
    SubAgentSpec,
    ToolDef,
)


class FakeProvider(LLMProvider):
    def __init__(self, events: list[LLMStreamEvent], final: PhaseResult):
        self.events = events
        self.final = final
        self.last_tools: list[ToolDef] = []
        self.last_subagents: list[SubAgentSpec] | None = None
        self.last_user_prompt: str = ""
        self.last_system_prompt: str = ""

    async def stream_phase(self, *, system_prompt, user_prompt, tools,
                          subagents=None, on_event, agent_name="planner",
                          max_turns=None, builtin_tools=None) -> PhaseResult:
        self.last_tools = tools
        self.last_subagents = subagents
        self.last_user_prompt = user_prompt
        self.last_system_prompt = system_prompt
        for ev in self.events:
            await on_event(ev)
        return self.final


async def test_provider_protocol_invokes_on_event_in_order():
    seen: list[str] = []

    async def on_event(ev: LLMStreamEvent) -> None:
        seen.append(ev.type)

    p = FakeProvider(
        events=[
            LLMTextDelta(agent="literature", message_id="m1", text="Searching..."),
            LLMToolCall(agent="literature", tool="search_arxiv", tool_call_id="t1",
                        input={"query": "RAG"}),
            LLMToolResult(tool_call_id="t1", tool="search_arxiv", ok=True,
                          output={"hits": 3}, duration_ms=120),
            LLMTextDelta(agent="literature", message_id="m1", text=" found 3."),
            LLMTurnComplete(agent="literature", final_text="Searching... found 3.",
                            input_tokens=100, output_tokens=20),
        ],
        final=PhaseResult(final_text="Searching... found 3.", input_tokens=100,
                          output_tokens=20, cache_read_tokens=0,
                          cache_write_tokens=0, total_cost_usd=0.001),
    )
    result = await p.stream_phase(
        system_prompt="sys", user_prompt="go", tools=[], on_event=on_event,
        agent_name="literature",
    )
    assert seen == ["text_delta", "tool_call", "tool_result", "text_delta", "turn_complete"]
    assert result.final_text == "Searching... found 3."
    assert result.input_tokens == 100


async def test_llm_events_translate_to_domain_events(store):
    """End-to-end: FakeProvider's stream → on_event handler → EventBus publish.

    This mirrors how the Planner will wire the provider into the bus.
    """
    bus = SQLiteEventBus(store)
    rid = await store.create_run("p")

    async def on_llm(ev: LLMStreamEvent) -> None:
        match ev.type:
            case "text_delta":
                await bus.publish(rid, LLMMessageDeltaEvent(
                    run_id=rid, agent=ev.agent, message_id=ev.message_id,
                    text_delta=ev.text,
                ))
            case "tool_call":
                await bus.publish(rid, LLMToolCallEvent(
                    run_id=rid, agent=ev.agent, tool=ev.tool,
                    tool_call_id=ev.tool_call_id, input_json=ev.input,
                ))
            case "tool_result":
                await bus.publish(rid, ToolResultEvent(
                    run_id=rid, tool_call_id=ev.tool_call_id, tool=ev.tool,
                    ok=ev.ok, output_json=ev.output, error=ev.error,
                    duration_ms=ev.duration_ms,
                ))
            case _:
                pass

    p = FakeProvider(
        events=[
            LLMTextDelta(agent="lit", message_id="m1", text="hi"),
            LLMToolCall(agent="lit", tool="search_arxiv", tool_call_id="t1",
                        input={"q": "x"}),
            LLMToolResult(tool_call_id="t1", tool="search_arxiv", ok=True,
                          output={"n": 1}, duration_ms=50),
        ],
        final=PhaseResult("hi", 1, 1, 0, 0, 0.0),
    )
    await p.stream_phase(
        system_prompt="", user_prompt="go", tools=[], on_event=on_llm,
    )
    rows = await store.events_since(rid, 0)
    types = [r.type for r in rows]
    assert types == ["llm.message_delta", "llm.tool_call", "tool.result"]
    assert rows[2].payload["ok"] is True
    assert rows[2].payload["duration_ms"] == 50


async def test_provider_captures_subagents_and_tools():
    p = FakeProvider(events=[], final=PhaseResult("", 0, 0, 0, 0, 0.0))

    async def _h(args):
        return {"ok": True}

    tools = [ToolDef(name="t1", description="x", input_schema={"type": "object"}, handler=_h)]
    subs = [SubAgentSpec(name="literature", description="d", prompt="p", tools=["t1"])]
    async def on_event(ev): pass

    await p.stream_phase(
        system_prompt="s", user_prompt="u", tools=tools, subagents=subs,
        on_event=on_event,
    )
    assert p.last_tools[0].name == "t1"
    assert p.last_subagents is not None
    assert p.last_subagents[0].name == "literature"
    # ToolDef.handler is callable
    out = await p.last_tools[0].handler({})
    assert out == {"ok": True}
