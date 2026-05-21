"""RunPod provider — mocked REST tests + a live test gated on `runpod_live`.

The live test provisions and terminates a real pod; it costs cents and is
skipped by default.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from auto_research.compute.base import PodSpec
from auto_research.compute.runpod import (
    RUNPOD_REST,
    RunPodProvider,
    _extract_ssh,
    _normalize_status,
)


@pytest.fixture
def fake_ssh_key(tmp_path) -> Path:
    p = tmp_path / "key"
    p.write_text("not-a-real-key")
    return p


def test_normalize_status_maps_provider_strings():
    assert _normalize_status("RUNNING") == "ready"
    assert _normalize_status("PROVISIONING") == "provisioning"
    assert _normalize_status("PENDING") == "provisioning"
    assert _normalize_status("EXITED") == "terminated"
    assert _normalize_status("FAILED") == "failed"
    assert _normalize_status("") == "provisioning"


def test_extract_ssh_from_portmappings():
    host, port, pip = _extract_ssh({
        "publicIp": "1.2.3.4",
        "portMappings": {"22/tcp": 12345, "8888/tcp": 23456},
    })
    assert (host, port, pip) == ("1.2.3.4", 12345, "1.2.3.4")


def test_extract_ssh_from_runtime_ports():
    host, port, pip = _extract_ssh({
        "publicIp": "5.6.7.8",
        "runtime": {"ports": [
            {"privatePort": 8888, "publicPort": 11111, "ip": "5.6.7.8"},
            {"privatePort": 22, "publicPort": 22222, "ip": "5.6.7.8"},
        ]},
    })
    assert (host, port, pip) == ("5.6.7.8", 22222, "5.6.7.8")


def test_extract_ssh_returns_none_when_missing():
    host, port, _ = _extract_ssh({})
    assert host is None and port is None


async def test_provision_posts_to_rest_with_bearer(fake_ssh_key):
    with respx.mock(base_url=RUNPOD_REST) as mock:
        route = mock.post("/pods").respond(
            json={"id": "pod-abc", "costPerHr": 0.30,
                  "publicIp": "1.2.3.4",
                  "portMappings": {"22/tcp": 9000}},
        )
        async with httpx.AsyncClient(
            base_url=RUNPOD_REST,
            headers={"Authorization": "Bearer fake", "Content-Type": "application/json"},
            timeout=10.0,
        ) as client:
            prov = RunPodProvider("fake", fake_ssh_key, client=client)
            pod = await prov.provision(PodSpec(gpu_type="NVIDIA RTX A5000", disk_gb=20))
        assert pod.id == "pod-abc"
        assert pod.ssh_port == 9000
        assert pod.usd_per_hour == 0.30
        assert route.called
        body = route.calls[0].request.content.decode()
        assert "NVIDIA RTX A5000" in body
        assert "22/tcp" in body
        # Bearer not leaked
        sent_auth = route.calls[0].request.headers["Authorization"]
        assert sent_auth == "Bearer fake"


async def test_provision_raises_when_response_missing_id(fake_ssh_key):
    with respx.mock(base_url=RUNPOD_REST) as mock:
        mock.post("/pods").respond(json={"error": "no quota"})
        async with httpx.AsyncClient(base_url=RUNPOD_REST, timeout=10.0,
                                     headers={"Authorization": "Bearer x"}) as client:
            prov = RunPodProvider("x", fake_ssh_key, client=client)
            with pytest.raises(RuntimeError, match="no id"):
                await prov.provision(PodSpec(gpu_type="A5000"))


async def test_get_status_normalizes(fake_ssh_key):
    with respx.mock(base_url=RUNPOD_REST) as mock:
        mock.get("/pods/p1").respond(json={"desiredStatus": "RUNNING"})
        async with httpx.AsyncClient(base_url=RUNPOD_REST, timeout=10.0,
                                     headers={"Authorization": "Bearer x"}) as client:
            prov = RunPodProvider("x", fake_ssh_key, client=client)
            assert await prov.get_status("p1") == "ready"


async def test_terminate_is_tolerant_to_404(fake_ssh_key):
    with respx.mock(base_url=RUNPOD_REST) as mock:
        mock.delete("/pods/gone").respond(404)
        async with httpx.AsyncClient(base_url=RUNPOD_REST, timeout=10.0,
                                     headers={"Authorization": "Bearer x"}) as client:
            prov = RunPodProvider("x", fake_ssh_key, client=client)
            await prov.terminate("gone")  # must not raise


async def test_list_active_skips_terminated(fake_ssh_key):
    with respx.mock(base_url=RUNPOD_REST) as mock:
        mock.get("/pods").respond(json={"data": [
            {"id": "alive", "desiredStatus": "RUNNING", "publicIp": "1.1.1.1",
             "portMappings": {"22/tcp": 22}, "costPerHr": 0.5,
             "machine": {"gpuTypeId": "A5000"}},
            {"id": "dead", "desiredStatus": "EXITED",
             "machine": {"gpuTypeId": "A5000"}},
        ]})
        async with httpx.AsyncClient(base_url=RUNPOD_REST, timeout=10.0,
                                     headers={"Authorization": "Bearer x"}) as client:
            prov = RunPodProvider("x", fake_ssh_key, client=client)
            pods = await prov.list_active()
    ids = [p.id for p in pods]
    assert ids == ["alive"]


@pytest.mark.runpod_live
async def test_provision_and_terminate_live():
    """Provisions the smallest available RunPod GPU and immediately terminates.

    Reads RUNPOD_API_KEY and RUNPOD_SSH_PRIVATE_KEY_PATH from env. Costs cents.
    Run with: uv run pytest -m runpod_live
    """
    import os

    api_key = os.environ.get("RUNPOD_API_KEY", "")
    key_path = Path(os.environ.get("RUNPOD_SSH_PRIVATE_KEY_PATH",
                                   "~/.ssh/runpod_ed25519")).expanduser()
    if not api_key:
        pytest.skip("RUNPOD_API_KEY not set")
    prov = RunPodProvider(api_key, key_path)
    pod = await prov.provision(PodSpec(
        gpu_type="NVIDIA RTX A5000",
        disk_gb=20,
        name="auto-research-smoke",
    ))
    try:
        ready = await prov.wait_ready(pod.id, timeout_s=600)
        result = await prov.exec(ready, "nvidia-smi -L")
        assert result.exit_code == 0
        assert "GPU 0" in result.stdout
    finally:
        await prov.terminate(pod.id)
    await prov.close()
