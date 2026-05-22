"""Live RunPod provision/terminate smoke test.

Proves the GPU-ID fix: provisions a cheap RTX A4000 pod with the verified
gpuTypeId, polls until ready (or status is unambiguous), then terminates.

Usage:
    uv run python scripts/live_test_runpod_provision.py
"""

from __future__ import annotations

import asyncio
import os
import stat
import tempfile
import time
from pathlib import Path

from dotenv import dotenv_values

from auto_research.compute.base import PodSpec
from auto_research.compute.runpod import RunPodProvider


GPU_TYPE = "NVIDIA RTX A5000"  # cheap verified id with broad availability; $0.16/hr
IMAGE = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"


def _materialize_ssh_key(env: dict[str, str | None]) -> Path:
    """Write RUNPOD_SSH_PRIVATE_KEY content to a 0600 temp file and return it."""
    key_blob = env.get("RUNPOD_SSH_PRIVATE_KEY")
    if not key_blob:
        raise SystemExit("RUNPOD_SSH_PRIVATE_KEY missing from .env")
    # Strip surrounding quotes if present
    key_blob = key_blob.strip()
    if key_blob.startswith('"') and key_blob.endswith('"'):
        key_blob = key_blob[1:-1]
    if not key_blob.endswith("\n"):
        key_blob += "\n"
    fd, path = tempfile.mkstemp(prefix="runpod_test_", suffix=".key")
    os.close(fd)
    p = Path(path)
    p.write_text(key_blob)
    try:
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass  # Windows ignores chmod bits; asyncssh still accepts the key
    return p


async def main() -> int:
    env = dotenv_values(".env")
    api_key = env.get("RUNPOD_API_KEY")
    if not api_key:
        raise SystemExit("RUNPOD_API_KEY missing from .env")
    api_key = api_key.strip().strip('"')

    ssh_key_path = _materialize_ssh_key(env)
    print(f"[setup] wrote ssh key to {ssh_key_path}")

    provider = RunPodProvider(
        api_key=api_key,
        ssh_private_key_path=ssh_key_path,
    )

    spec = PodSpec(
        gpu_type=GPU_TYPE,
        image=IMAGE,
        disk_gb=20,
        name=f"auto-research-gpu-id-test-{int(time.time())}",
    )

    pod_id: str | None = None
    try:
        print(f"[provision] requesting {GPU_TYPE} pod ...")
        pod = await provider.provision(spec)
        pod_id = pod.id
        print(f"[provision] OK pod_id={pod_id} ${pod.usd_per_hour:.2f}/hr")

        print("[wait] polling status (provider only, skipping SSH probe) ...")
        # We only need to prove the pod was *accepted* and reached RUNNING — the
        # SSH probe in wait_ready() requires the pod's SSH key to be registered
        # on the RunPod account, which is a separate concern from the GPU-ID bug.
        deadline = time.monotonic() + 300
        terminal_ok = False
        while time.monotonic() < deadline:
            meta = await provider._get_pod_meta(pod_id)  # type: ignore[attr-defined]
            status = (meta.get("desiredStatus") or meta.get("status") or "").upper()
            print(f"[wait] status={status!r}")
            if status == "RUNNING":
                terminal_ok = True
                break
            if status in {"FAILED", "ERROR", "TERMINATED", "EXITED", "STOPPED"}:
                raise SystemExit(f"pod entered terminal state: {status}")
            await asyncio.sleep(5)
        if not terminal_ok:
            raise SystemExit("pod did not reach RUNNING within 300s")
        print("[ready] pod is RUNNING — GPU id was accepted by RunPod")
    finally:
        if pod_id:
            print(f"[teardown] terminating {pod_id} ...")
            await provider.terminate(pod_id)
            print("[teardown] terminated.")
        await provider.close()
        try:
            ssh_key_path.unlink()
        except OSError:
            pass

    print("[done] live test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
