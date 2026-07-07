"""Regression tests for the NDG cache refresh (failed on 2nd run in v1.2.0)."""
import asyncio
from unittest.mock import patch, AsyncMock

from app.db import init_db
from app import ndg

init_db()


def _run(names):
    fake = [{"name": n, "id": str(i)} for i, n in enumerate(names)]
    with patch.object(ndg, "ISEClient") as mock_ise:
        inst = mock_ise.return_value
        inst.__aenter__ = AsyncMock(return_value=inst)
        inst.__aexit__ = AsyncMock(return_value=False)
        inst.list_ndgs = AsyncMock(return_value=fake)
        return asyncio.run(ndg.refresh_ndg_cache())


NAMES = ([f"Device Type#All Device Types#G{i}" for i in range(120)]
         + [f"Location#All Locations#L{i}" for i in range(17)])


def test_refresh_is_idempotent():
    first = _run(NAMES)
    assert first["total"] == 137
    second = _run(NAMES)  # raised StaleDataError before the fix
    assert second["total"] == 137
    assert second["removed"] == 0
    assert second["device_types"] == 120
    assert second["locations"] == 17


def test_refresh_prunes_vanished_ndgs():
    _run(NAMES)
    result = _run(NAMES[:-2])
    assert result["removed"] == 2
    assert len(ndg.get_cached_ndgs()) == 135


def test_empty_ise_answer_never_wipes_cache():
    _run(NAMES)
    result = _run([])
    assert result["removed"] == 0
    assert len(ndg.get_cached_ndgs()) >= 135
