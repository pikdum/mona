from datetime import datetime
from functools import wraps


class Cache:
    def __init__(self):
        self.store = {}

    def get(self, key):
        entry = self.store.get(key)
        if entry:
            if entry["expires"] is None or entry["expires"] > datetime.now():
                return entry["value"]
            del self.store[key]
        return None

    def set(self, key, value, timeout=None):
        expires = datetime.now() + timeout if timeout else None
        self.store[key] = {"value": value, "expires": expires}
        self._cleanup_expired()

    def delete(self, key):
        if key in self.store:
            del self.store[key]

    def _cleanup_expired(self):
        now = datetime.now()
        expired_keys = [
            key
            for key, entry in self.store.items()
            if entry["expires"] is not None and entry["expires"] <= now
        ]
        for key in expired_keys:
            del self.store[key]


class Memoize:
    def __init__(self, cache):
        self.cache = cache

    def __call__(self, func):
        @wraps(func)
        async def memoizer(*args, **kwargs):
            key = (func.__name__, args, frozenset(kwargs.items()))
            if result := self.cache.get(key):
                return result
            result = await func(*args, **kwargs)
            self.cache.set(key, result)
            return result

        return memoizer
