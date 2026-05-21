"""Discriminated union of every event the agent emits.

Every UI element in the live monitor is driven by exactly one of these. The
event row id (assigned by SQLite on INSERT) is the SSE `Last-Event-ID` cursor.
"""

from __future__ import annotations

import time
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

Verdict = Literal["conclusive", "debunked", "inconclusive", "refine"]
PodStatus = Literal["provisioning", "ready", "running", "terminated", "failed"]


class _Base(BaseModel):
    model_config = ConfigDict(frozen=False, extra="forbid")

    id: int | None = None
    run_id: str
    ts: float = Field(default_factory=time.time)


class PhaseTransitionEvent(_Base):
    type: Literal["phase.transition"] = "phase.transition"
    from_phase: str
    to_phase: str
    reason: str = ""
    refinement_round: int = 0


class LLMMessageDeltaEvent(_Base):
    type: Literal["llm.message_delta"] = "llm.message_delta"
    agent: str
    role: Literal["assistant", "user", "system"] = "assistant"
    text_delta: str
    message_id: str


class LLMToolCallEvent(_Base):
    type: Literal["llm.tool_call"] = "llm.tool_call"
    agent: str
    tool: str
    tool_call_id: str
    input_json: dict[str, Any]


class ToolResultEvent(_Base):
    type: Literal["tool.result"] = "tool.result"
    tool_call_id: str
    tool: str
    ok: bool
    output_json: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: int = 0


class CitationAddedEvent(_Base):
    type: Literal["citation.added"] = "citation.added"
    key: str
    title: str
    authors: list[str]
    year: int | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    url: str
    pdf_path: str | None = None


class PodProvisionedEvent(_Base):
    type: Literal["pod.provisioned"] = "pod.provisioned"
    pod_id: str
    runpod_id: str
    gpu: str
    usd_per_hour: float
    ssh_host: str
    ssh_port: int


class PodStatusEvent(_Base):
    type: Literal["pod.status"] = "pod.status"
    pod_id: str
    status: PodStatus


class PodLogEvent(_Base):
    type: Literal["pod.log"] = "pod.log"
    pod_id: str
    stream: Literal["stdout", "stderr"] = "stdout"
    lines: list[str]


class ExperimentMetricEvent(_Base):
    type: Literal["experiment.metric"] = "experiment.metric"
    pod_id: str
    experiment_id: str
    step: int
    metrics: dict[str, float]


class FileWrittenEvent(_Base):
    type: Literal["file.written"] = "file.written"
    path: str
    kind: Literal[
        "pdf", "dataset", "script", "report_md", "report_tex", "report_pdf",
        "figure", "checkpoint", "bibtex", "other"
    ]
    sha256: str
    size_bytes: int


class CostTickEvent(_Base):
    type: Literal["cost.tick"] = "cost.tick"
    total_usd: float
    by_pod: dict[str, float] = Field(default_factory=dict)


class AnalystVerdictEvent(_Base):
    type: Literal["analyst.verdict"] = "analyst.verdict"
    refinement_round: int
    verdict: Verdict
    rationale: str
    next_actions: list[str] = Field(default_factory=list)


class ErrorEvent(_Base):
    type: Literal["error"] = "error"
    where: str
    message: str
    traceback: str | None = None
    fatal: bool = False


Event = Annotated[
    Union[
        PhaseTransitionEvent,
        LLMMessageDeltaEvent,
        LLMToolCallEvent,
        ToolResultEvent,
        CitationAddedEvent,
        PodProvisionedEvent,
        PodStatusEvent,
        PodLogEvent,
        ExperimentMetricEvent,
        FileWrittenEvent,
        CostTickEvent,
        AnalystVerdictEvent,
        ErrorEvent,
    ],
    Field(discriminator="type"),
]

_EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)


def parse_event(payload: dict[str, Any] | str) -> Event:
    if isinstance(payload, str):
        return _EVENT_ADAPTER.validate_json(payload)
    return _EVENT_ADAPTER.validate_python(payload)
