import pytest

from auto_research.state.store import StateStore


@pytest.fixture
async def store(tmp_path):
    s = StateStore(tmp_path / "state.db")
    await s.open()
    try:
        yield s
    finally:
        await s.close()
