"""Provider-agnostic GPU compute interface.

`Pod` is the immutable identity returned by `provision`; mutable status
information lives on the provider side and is fetched via `get_status`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


PodStatus = Literal["provisioning", "ready", "running", "terminated", "failed"]


@dataclass(frozen=True)
class PodSpec:
    gpu_type: str
    gpu_count: int = 1
    image: str = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"
    disk_gb: int = 50
    volume_gb: int = 0
    ports: tuple[int, ...] = (22,)
    env: dict[str, str] = field(default_factory=dict)
    name: str = "auto-research-pod"
    datacenter_id: str | None = None
    network_volume_id: str | None = None


@dataclass(frozen=True)
class Pod:
    id: str                       # provider-side id
    spec: PodSpec
    usd_per_hour: float
    ssh_host: str | None = None
    ssh_port: int | None = None
    public_ip: str | None = None
    provider_meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int


class ComputeProvider(ABC):
    @abstractmethod
    async def provision(self, spec: PodSpec) -> Pod: ...

    @abstractmethod
    async def get_status(self, pod_id: str) -> PodStatus: ...

    @abstractmethod
    async def wait_ready(self, pod_id: str, timeout_s: int = 600) -> Pod:
        """Poll provider status AND verify SSH + /workspace are usable before returning."""

    @abstractmethod
    async def exec(
        self, pod: Pod, cmd: str, *, cwd: str = "/workspace", timeout_s: int = 600,
    ) -> ExecResult: ...

    @abstractmethod
    async def stream_logs(
        self, pod: Pod, remote_path: str,
    ) -> AsyncIterator[list[str]]:
        """tail -F a remote file, yielding 100ms-batched line lists."""

    @abstractmethod
    async def upload(self, pod: Pod, local: Path, remote: str) -> None: ...

    @abstractmethod
    async def download(self, pod: Pod, remote: str, local: Path) -> None: ...

    @abstractmethod
    async def terminate(self, pod_id: str) -> None: ...

    @abstractmethod
    async def list_active(self) -> list[Pod]: ...
