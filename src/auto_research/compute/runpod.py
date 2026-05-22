"""RunPod compute provider.

REST: ``https://rest.runpod.io/v1/pods`` with Bearer token. SSH via
``asyncssh`` using the user-provided private key. ``wait_ready`` polls both
provider status AND a ``test -d /workspace`` over SSH before returning.

This module never logs the API key. The asyncssh connection accepts the pod's
host key on first contact (known_hosts is not pre-populated for ephemeral pods).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import asyncssh
import httpx

from auto_research.compute.base import (
    ComputeProvider,
    ExecResult,
    Pod,
    PodSpec,
    PodStatus,
)
from auto_research.logging import get_logger

_log = get_logger(__name__)

RUNPOD_REST = "https://rest.runpod.io/v1"


def _normalize_status(provider: str) -> PodStatus:
    s = (provider or "").upper()
    if s in {"RUNNING"}:
        return "ready"
    if s in {"CREATED", "STARTING", "PROVISIONING", "PENDING"}:
        return "provisioning"
    if s in {"EXITED", "TERMINATED", "STOPPED"}:
        return "terminated"
    if s in {"FAILED", "ERROR"}:
        return "failed"
    return "provisioning"


def _extract_ssh(meta: dict[str, Any]) -> tuple[str | None, int | None, str | None]:
    """Pull (host, port, public_ip) out of a /v1/pods/{id} response.

    RunPod surfaces port mappings under `portMappings`/`runtime.ports`/`publicIp`
    depending on API revision. We try the most stable shape first.
    """
    public_ip = meta.get("publicIp") or meta.get("publicIP")
    # New REST shape: portMappings is dict { "22/tcp": 12345 } or similar
    pm = meta.get("portMappings") or {}
    if isinstance(pm, dict):
        for k, v in pm.items():
            if str(k).startswith("22"):
                try:
                    return (public_ip, int(v), public_ip)
                except (ValueError, TypeError):
                    pass
    # Legacy: runtime.ports = [{"privatePort":22,"publicPort":N,"ip":"..."}]
    runtime = meta.get("runtime") or {}
    ports = runtime.get("ports") or []
    for p in ports:
        if int(p.get("privatePort", 0)) == 22:
            return (p.get("ip") or public_ip, int(p.get("publicPort", 0)) or None, public_ip)
    return (public_ip, 22 if public_ip else None, public_ip)


class RunPodProvider(ComputeProvider):
    def __init__(
        self,
        api_key: str,
        ssh_private_key_path: Path,
        *,
        client: httpx.AsyncClient | None = None,
        datacenter_id: str | None = None,
        network_volume_id: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._ssh_key_path = Path(ssh_private_key_path).expanduser()
        self._own_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=RUNPOD_REST,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            timeout=30.0,
        )
        self._default_dc = datacenter_id
        self._default_volume = network_volume_id

    async def close(self) -> None:
        if self._own_client:
            await self.client.aclose()

    # -------------------------------------------------------------- lifecycle

    async def provision(self, spec: PodSpec) -> Pod:
        payload: dict[str, Any] = {
            "name": spec.name,
            "imageName": spec.image,
            "gpuTypeIds": [spec.gpu_type],
            "gpuCount": spec.gpu_count,
            "containerDiskInGb": spec.disk_gb,
            "ports": [f"{p}/tcp" for p in spec.ports],
            # /v1/pods schema wants env as a {key:value} object, not [{key,value}, ...]
            "env": dict(spec.env),
        }
        if spec.volume_gb > 0:
            payload["volumeInGb"] = spec.volume_gb
        if spec.network_volume_id or self._default_volume:
            payload["networkVolumeId"] = spec.network_volume_id or self._default_volume
        if spec.datacenter_id or self._default_dc:
            payload["dataCenterIds"] = [spec.datacenter_id or self._default_dc]

        r = await self.client.post("/pods", json=payload)
        r.raise_for_status()
        data = r.json()
        pod_id = str(data.get("id") or data.get("pod", {}).get("id") or "")
        if not pod_id:
            raise RuntimeError(f"RunPod create returned no id: {data!r}")
        usd_per_hour = float(
            data.get("costPerHr") or data.get("machine", {}).get("costPerHr") or 0.0
        )
        host, port, pip = _extract_ssh(data)
        return Pod(id=pod_id, spec=spec, usd_per_hour=usd_per_hour,
                   ssh_host=host, ssh_port=port, public_ip=pip,
                   provider_meta=data)

    async def get_status(self, pod_id: str) -> PodStatus:
        r = await self.client.get(f"/pods/{pod_id}")
        r.raise_for_status()
        return _normalize_status(r.json().get("desiredStatus") or r.json().get("status", ""))

    async def _get_pod_meta(self, pod_id: str) -> dict[str, Any]:
        r = await self.client.get(f"/pods/{pod_id}")
        r.raise_for_status()
        return r.json()

    async def wait_ready(self, pod_id: str, timeout_s: int = 600) -> Pod:
        deadline = time.monotonic() + timeout_s
        last_meta: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last_meta = await self._get_pod_meta(pod_id)
            status = _normalize_status(
                last_meta.get("desiredStatus") or last_meta.get("status", "")
            )
            if status == "ready":
                host, port, pip = _extract_ssh(last_meta)
                if host and port:
                    spec = PodSpec(
                        gpu_type=str(
                            (last_meta.get("machine") or {}).get("gpuTypeId")
                            or (last_meta.get("gpuTypeIds") or ["unknown"])[0]
                        ),
                    )
                    pod = Pod(
                        id=pod_id, spec=spec,
                        usd_per_hour=float(last_meta.get("costPerHr") or 0.0),
                        ssh_host=host, ssh_port=port, public_ip=pip,
                        provider_meta=last_meta,
                    )
                    # SSH probe: connect + verify workspace dir exists
                    try:
                        probe = await self.exec(pod, "test -d /workspace && nvidia-smi -L",
                                                timeout_s=20)
                        if probe.exit_code == 0:
                            return pod
                    except Exception as exc:  # noqa: BLE001
                        _log.debug("runpod.ssh_probe_failed", pod_id=pod_id, error=str(exc))
            elif status in {"failed", "terminated"}:
                raise RuntimeError(f"Pod {pod_id} entered terminal state: {status}")
            await asyncio.sleep(5.0)
        raise TimeoutError(f"Pod {pod_id} not ready within {timeout_s}s; last={last_meta!r}")

    async def terminate(self, pod_id: str) -> None:
        try:
            r = await self.client.delete(f"/pods/{pod_id}")
            if r.status_code in {404, 410}:
                return
            r.raise_for_status()
        except httpx.HTTPError as exc:
            _log.warning("runpod.terminate_failed", pod_id=pod_id, error=str(exc))

    async def list_active(self) -> list[Pod]:
        r = await self.client.get("/pods")
        r.raise_for_status()
        rows = r.json() if isinstance(r.json(), list) else r.json().get("data", [])
        out: list[Pod] = []
        for row in rows:
            if _normalize_status(row.get("desiredStatus") or "") in {"terminated", "failed"}:
                continue
            host, port, pip = _extract_ssh(row)
            spec = PodSpec(gpu_type=str(
                (row.get("machine") or {}).get("gpuTypeId")
                or (row.get("gpuTypeIds") or ["unknown"])[0]
            ))
            out.append(Pod(
                id=str(row.get("id")), spec=spec,
                usd_per_hour=float(row.get("costPerHr") or 0.0),
                ssh_host=host, ssh_port=port, public_ip=pip,
                provider_meta=row,
            ))
        return out

    # --------------------------------------------------------------------- ssh

    async def _connect(self, pod: Pod) -> asyncssh.SSHClientConnection:
        if not pod.ssh_host or not pod.ssh_port:
            raise RuntimeError(f"Pod {pod.id} has no SSH endpoint yet")
        return await asyncssh.connect(
            pod.ssh_host,
            port=pod.ssh_port,
            username="root",
            client_keys=[str(self._ssh_key_path)],
            known_hosts=None,  # ephemeral pods; first-contact trust
        )

    async def exec(
        self, pod: Pod, cmd: str, *, cwd: str = "/workspace", timeout_s: int = 600,
    ) -> ExecResult:
        wrapped = f"mkdir -p {cwd} && cd {cwd} && {cmd}"
        t0 = time.time()
        async with await self._connect(pod) as conn:
            result = await asyncio.wait_for(
                conn.run(wrapped, check=False), timeout=timeout_s,
            )
        return ExecResult(
            exit_code=int(result.exit_status or 0),
            stdout=str(result.stdout or ""),
            stderr=str(result.stderr or ""),
            duration_ms=int((time.time() - t0) * 1000),
        )

    async def stream_logs(
        self, pod: Pod, remote_path: str,
    ) -> AsyncIterator[list[str]]:
        """tail -F remote_path, yielding 100ms-batched line lists."""
        async with await self._connect(pod) as conn:
            proc = await conn.create_process(f"tail -F -n +1 {remote_path}")
            buf: list[str] = []
            last_flush = time.monotonic()
            async for line in proc.stdout:
                buf.append(str(line).rstrip("\n"))
                now = time.monotonic()
                if now - last_flush >= 0.1 and buf:
                    yield buf
                    buf, last_flush = [], now
            if buf:
                yield buf

    async def upload(self, pod: Pod, local: Path, remote: str) -> None:
        async with await self._connect(pod) as conn:
            await asyncssh.scp(str(local), (conn, remote), recurse=local.is_dir())

    async def download(self, pod: Pod, remote: str, local: Path) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        async with await self._connect(pod) as conn:
            await asyncssh.scp((conn, remote), str(local), recurse=True)
