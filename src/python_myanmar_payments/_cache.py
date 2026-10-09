"""Access token caches (used by Yoma MMQR)."""

from __future__ import annotations

import threading
import time
from typing import Protocol, runtime_checkable

__all__ = ["AsyncTokenCache", "MemoryTokenCache", "TokenCache"]


@runtime_checkable
class TokenCache(Protocol):
    """Stores access tokens between calls.

    Implement it on top of Redis or similar to share tokens between processes.
    """

    def get(self, key: str) -> str | None:
        """The cached value, or ``None`` when it is missing or expired."""

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        """Stores ``value`` for ``ttl_seconds``; ``0`` or less never expires."""

    def delete(self, key: str) -> None:
        """Removes ``key``."""


@runtime_checkable
class AsyncTokenCache(Protocol):
    """A :class:`TokenCache` with coroutine methods, e.g. on ``redis.asyncio``.

    Only :class:`~python_myanmar_payments.AsyncYomaMmqr` accepts it.
    """

    async def get(self, key: str) -> str | None:
        """The cached value, or ``None`` when it is missing or expired."""

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        """Stores ``value`` for ``ttl_seconds``; ``0`` or less never expires."""

    async def delete(self, key: str) -> None:
        """Removes ``key``."""


class MemoryTokenCache:
    """The default :class:`TokenCache`: a dict that lives as long as the process.

    Share one gateway (or one cache) across requests so the token is reused.
    Thread-safe.
    """

    def __init__(self) -> None:
        self._items: dict[str, tuple[str, float | None]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            value, expires_at = item
            if expires_at is not None and time.monotonic() >= expires_at:
                del self._items[key]
                return None
            return value

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        with self._lock:
            expires_at = time.monotonic() + ttl_seconds if ttl_seconds > 0 else None
            self._items[key] = (value, expires_at)

    def delete(self, key: str) -> None:
        with self._lock:
            self._items.pop(key, None)

    def clear(self) -> None:
        """Removes every entry."""
        with self._lock:
            self._items.clear()
