"""Analyst sub-agent — judges the verdict for the current round."""

from __future__ import annotations

from auto_research.llm.base import SubAgentSpec

ANALYST_SYSTEM_PROMPT = """\
You are the ANALYST sub-agent inside an autonomous research pipeline.

Given (a) the problem statement, (b) the experiment results for this round \
(metrics, exit codes, durations), and (c) any prior round summaries, judge \
whether the evidence is sufficient to conclude.

Emit your verdict as the LAST message in this EXACT JSON form:

{
  "verdict": "conclusive" | "debunked" | "inconclusive" | "refine",
  "rationale": "<honest one-paragraph reasoning>",
  "next_actions": ["...", "..."]
}

Rules:
- "conclusive": the hypothesis is supported with adequate evidence; proceed to WRITE.
- "debunked": the hypothesis is contradicted with adequate evidence; proceed to WRITE.
- "inconclusive": results are too noisy or coverage is too thin; refine + re-run.
- "refine": partial signal, needs hyperparameter sweep or new variant.

For the v1 vertical slice, prefer "conclusive" when at least one experiment \
completed and produced sensible metrics — we want the smoke run to finish, \
not loop forever. Refinement is hard-capped at 3 rounds upstream.
"""


def analyst_subagent() -> SubAgentSpec:
    return SubAgentSpec(
        name="analyst",
        description="Judges experiment results and emits a structured verdict.",
        prompt=ANALYST_SYSTEM_PROMPT,
        tools=[],
    )
