from datetime import datetime
from functools import wraps


class Cache:
    def __init__(self):
        self.store = {}

    def get(self, key):
        entry = self.store.get(key)
        if entry and (entry["expires"] is None or entry["expires"] > datetime.now()):
            return entry["value"]
        return None

    def set(self, key, value, timeout=None):
        expires = datetime.now() + timeout if timeout else None
        self.store[key] = {"value": value, "expires": expires}

    def delete(self, key):
        if key in self.store:
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
