"""Pod runner — deterministic coroutine, NOT an SDK sub-agent.

Pod lifecycle is too expensive/stateful to be re-derived by an LLM each turn,
so the planner calls this directly. It owns: provision → upload script →
exec → stream logs+metrics → download artifacts → terminate.

Metric streaming: the remote script appends JSONL lines to a known path; this
runner `tail -F`s the path and emits `experiment.metric` events as lines
arrive. Stdout/stderr are emitted as `pod.log` events.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from auto_research.compute.base import ComputeProvider, Pod, PodSpec
from auto_research.events.bus import EventBus
from auto_research.events.schemas import (
    CostTickEvent,
    ExperimentMetricEvent,
    PodLogEvent,
    PodProvisionedEvent,
    PodStatusEvent,
)
from auto_research.logging import get_logger

_log = get_logger(__name__)


@dataclass(frozen=True)
class ExperimentSpec:
    experiment_id: str
    pod_spec: PodSpec
    script: str                          # entire python script body
    requirements: list[str] = field(default_factory=list)
    metrics_path: str = "/workspace/metrics.jsonl"
    log_path: str = "/workspace/run.log"
    timeout_s: int = 3600


@dataclass(frozen=True)
class ExperimentResult:
    experiment_id: str
    pod_id: str
    exit_code: int
    metrics: list[dict[str, Any]]
    artifact_paths: list[Path] = field(default_factory=list)
    duration_s: float = 0.0


async def _cost_ticker(
    bus: EventBus, run_id: str, pod: Pod, started_at: float, stop: asyncio.Event,
    *, interval_s: float = 30.0,
) -> None:
    async def _emit() -> None:
        elapsed_h = (time.time() - started_at) / 3600
        cost = pod.usd_per_hour * elapsed_h
        await bus.publish(run_id, CostTickEvent(
            run_id=run_id, total_usd=cost, by_pod={pod.id: cost},
        ))

    await _emit()  # initial tick so short runs still show a cost event
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
            break
        except asyncio.TimeoutError:
            await _emit()
    await _emit()  # final tick captures total elapsed


async def _log_streamer(
    bus: EventBus, run_id: str, provider: ComputeProvider, pod: Pod,
    remote_path: str, stop: asyncio.Event,
) -> None:
    try:
        async for batch in provider.stream_logs(pod, remote_path):
            if stop.is_set():
                break
            await bus.publish(run_id, PodLogEvent(
                run_id=run_id, pod_id=pod.id, lines=batch,
            ))
    except Exception as exc:  # noqa: BLE001
        _log.warning("pod_runner.log_stream_failed", pod_id=pod.id, error=str(exc))


async def _metric_streamer(
    bus: EventBus, run_id: str, provider: ComputeProvider, pod: Pod,
    spec: ExperimentSpec, collected: list[dict[str, Any]], stop: asyncio.Event,
) -> None:
    try:
        async for batch in provider.stream_logs(pod, spec.metrics_path):
            if stop.is_set():
                break
            for line in batch:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                step = int(obj.pop("step", len(collected)))
                metrics = {k: float(v) for k, v in obj.items()
                           if isinstance(v, (int, float))}
                collected.append({"step": step, **metrics})
                await bus.publish(run_id, ExperimentMetricEvent(
                    run_id=run_id, pod_id=pod.id,
                    experiment_id=spec.experiment_id, step=step, metrics=metrics,
                ))
    except Exception as exc:  # noqa: BLE001
        _log.debug("pod_runner.metric_stream_failed", pod_id=pod.id, error=str(exc))


async def run_experiment(
    *,
    spec: ExperimentSpec,
    provider: ComputeProvider,
    bus: EventBus,
    run_id: str,
    workspace_dir: Path,
) -> ExperimentResult:
    """Provision pod → upload+run script → stream logs+metrics → terminate.

    Caller is responsible for catching errors and handling them; this function
    guarantees that the pod is always terminated (best-effort) before returning.
    """
    t0 = time.time()
    pod = await provider.provision(spec.pod_spec)
    await bus.publish(run_id, PodProvisionedEvent(
        run_id=run_id, pod_id=pod.id, runpod_id=pod.id,
        gpu=spec.pod_spec.gpu_type, usd_per_hour=pod.usd_per_hour,
        ssh_host=pod.ssh_host or "", ssh_port=pod.ssh_port or 0,
    ))
    await bus.publish(run_id, PodStatusEvent(
        run_id=run_id, pod_id=pod.id, status="provisioning",
    ))

    try:
        ready = await provider.wait_ready(pod.id)
        await bus.publish(run_id, PodStatusEvent(
            run_id=run_id, pod_id=pod.id, status="ready",
        ))

        # Stage the script
        script_local = workspace_dir / f"{spec.experiment_id}.py"
        script_local.parent.mkdir(parents=True, exist_ok=True)
        script_local.write_text(spec.script)
        await provider.upload(ready, script_local, "/workspace/experiment.py")

        if spec.requirements:
            req_line = " ".join(spec.requirements)
            await provider.exec(ready, f"pip install --quiet {req_line}", timeout_s=600)

        # Start streamers + cost ticker
        stop = asyncio.Event()
        metrics_collected: list[dict[str, Any]] = []
        tasks = [
            asyncio.create_task(_log_streamer(
                bus, run_id, provider, ready, spec.log_path, stop)),
            asyncio.create_task(_metric_streamer(
                bus, run_id, provider, ready, spec, metrics_collected, stop)),
            asyncio.create_task(_cost_ticker(bus, run_id, ready, t0, stop)),
        ]

        await bus.publish(run_id, PodStatusEvent(
            run_id=run_id, pod_id=pod.id, status="running",
        ))
        try:
            exec_cmd = (
                f"touch {spec.log_path} {spec.metrics_path} && "
                f"python /workspace/experiment.py > {spec.log_path} 2>&1"
            )
            result = await provider.exec(ready, exec_cmd, timeout_s=spec.timeout_s)
        finally:
            stop.set()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        # Download artifacts (metrics.jsonl + log) for the record
        artifact_dir = workspace_dir / "experiments" / spec.experiment_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifacts: list[Path] = []
        for remote, local in [
            (spec.metrics_path, artifact_dir / "metrics.jsonl"),
            (spec.log_path, artifact_dir / "run.log"),
        ]:
            try:
                await provider.download(ready, remote, local)
                artifacts.append(local)
            except Exception as exc:  # noqa: BLE001
                _log.warning("pod_runner.download_failed",
                             pod_id=pod.id, remote=remote, error=str(exc))

        return ExperimentResult(
            experiment_id=spec.experiment_id,
            pod_id=pod.id,
            exit_code=result.exit_code,
            metrics=metrics_collected,
            artifact_paths=artifacts,
            duration_s=time.time() - t0,
        )
    finally:
        try:
            await provider.terminate(pod.id)
            await bus.publish(run_id, PodStatusEvent(
                run_id=run_id, pod_id=pod.id, status="terminated",
            ))
        except Exception as exc:  # noqa: BLE001
            _log.warning("pod_runner.terminate_failed", pod_id=pod.id, error=str(exc))
