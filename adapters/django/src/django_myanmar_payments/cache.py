"""Yoma MMQR token caches on Django's cache framework."""

from __future__ import annotations

from django.core.cache import caches

__all__ = ["AsyncDjangoTokenCache", "DjangoTokenCache"]


def _timeout(ttl_seconds: int) -> int | None:
    # The core's ``0 or less never expires`` is Django's ``None``; Django's 0 expires at once.
    return ttl_seconds if ttl_seconds > 0 else None


class DjangoTokenCache:
    """A core ``TokenCache`` on a ``CACHES`` alias, e.g. Redis.

    Every worker process then shares one Yoma MMQR access token. Django applies
    the cache's ``KEY_PREFIX`` and ``VERSION``, so only Django processes with the
    same cache settings share it.
    """

    def __init__(self, alias: str = "default") -> None:
        self.alias = alias

    def get(self, key: str) -> str | None:
        value = caches[self.alias].get(key)
        return value if isinstance(value, str) else None

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        caches[self.alias].set(key, value, timeout=_timeout(ttl_seconds))

    def delete(self, key: str) -> None:
        caches[self.alias].delete(key)


class AsyncDjangoTokenCache:
    """The core ``AsyncTokenCache`` twin of :class:`DjangoTokenCache`.

    Uses the cache's ``aget``/``aset``/``adelete``, so async gateways never block
    the event loop on the cache.
    """

    def __init__(self, alias: str = "default") -> None:
        self.alias = alias

    async def get(self, key: str) -> str | None:
        value = await caches[self.alias].aget(key)
        return value if isinstance(value, str) else None

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        await caches[self.alias].aset(key, value, timeout=_timeout(ttl_seconds))

    async def delete(self, key: str) -> None:
        await caches[self.alias].adelete(key)
