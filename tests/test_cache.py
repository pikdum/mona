from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from mona.cache import Cache, Memoize


@pytest.fixture
def cache():
    return Cache()


@pytest.fixture
def memoize(cache):
    return Memoize(cache)


def test_cache_set_and_get(cache):
    cache.set("key", "value")
    assert cache.get("key") == "value"


def test_cache_expiry(cache):
    with patch("mona.cache.datetime") as mock_datetime:
        mock_datetime.now.return_value = datetime(2023, 1, 1, 12, 0, 0)
        cache.set("key", "value", timedelta(seconds=1))
        assert cache.get("key") == "value"

        # Simulate time passing by advancing the mock
        mock_datetime.now.return_value = datetime(2023, 1, 1, 12, 0, 2)
        assert cache.get("key") is None


def test_cache_override(cache):
    cache.set("key", "value")
    cache.set("key", "new_value")
    assert cache.get("key") == "new_value"


def test_cache_delete(cache):
    cache.set("key", "value")
    cache.delete("key")
    assert cache.get("key") is None


@pytest.mark.asyncio
async def test_memoize_calls_function(memoize):
    call_count = {"count": 0}

    @memoize
    async def test_func(x):
        call_count["count"] += 1
        return x + 1

    assert await test_func(2) == 3
    assert call_count["count"] == 1
    # Call again with same argument, should not increase call count
    assert await test_func(2) == 3
    assert call_count["count"] == 1


@pytest.mark.asyncio
async def test_memoize_different_arguments(memoize):
    @memoize
    async def test_func(x):
        return x + 1

    result_1 = await test_func(2)
    result_2 = await test_func(3)
    assert result_1 == 3
    assert result_2 == 4


@pytest.mark.asyncio
async def test_memoize_cache_invalidation(memoize):
    @memoize
    async def test_func(x):
        return x + 1

    result_1 = await test_func(2)
    assert result_1 == 3

    # Invalidate cache for specific argument
    memoize.cache.delete(("test_func", (2,), frozenset()))
    result_2 = await test_func(2)
    assert result_2 == 3
