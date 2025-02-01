import pytest
from datetime import timedelta
from mona.cache import Cache

@pytest.fixture
def cache():
    return Cache()


def test_cache_set_and_get(cache):
    cache.set('key', 'value')
    assert cache.get('key') == 'value'


def test_cache_expiry(cache):
    cache.set('key', 'value', timedelta(seconds=1))
    assert cache.get('key') == 'value'

    # Simulate time passing
    import time
    time.sleep(1.1)
    assert cache.get('key') is None


def test_cache_override(cache):
    cache.set('key', 'value')
    cache.set('key', 'new_value')
    assert cache.get('key') == 'new_value'


def test_cache_delete(cache):
    cache.set('key', 'value')
    cache.delete('key')
    assert cache.get('key') is None
