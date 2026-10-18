"""The process's payments facades, built from ``MYANMAR_PAYMENTS`` on first use."""

from __future__ import annotations

import asyncio
import threading
import weakref
from typing import Any

import httpx
from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.utils.asyncio import async_unsafe
from python_myanmar_payments import AsyncMyanmarPayments, MemoryTokenCache, MyanmarPayments

from .cache import AsyncDjangoTokenCache, DjangoTokenCache
from .conf import PaymentsSettings, import_factory, payments_settings

__all__ = ["aclose_payments", "close_payments", "get_async_payments", "get_payments"]

_SyncEntry = tuple[MyanmarPayments, httpx.Client | None]
_AsyncEntry = tuple[AsyncMyanmarPayments, httpx.AsyncClient | None]


class _State:
    """The facades built from the current settings; :meth:`reset` when they change."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> _SyncEntry | None:
        """Forgets every facade and returns the sync one for closing."""
        with self.lock:
            previous = getattr(self, "sync", None)
            self.sync: _SyncEntry | None = None
            # An httpx.AsyncClient belongs to the loop it first ran on, so each event
            # loop gets its own facade, dropped with the loop.
            self.by_loop: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _AsyncEntry] = (
                weakref.WeakKeyDictionary()
            )
            # Shared by every facade, so all of them reuse one Yoma MMQR token.
            self.memory_cache = MemoryTokenCache()
        return previous


_state = _State()


@async_unsafe
def get_payments() -> MyanmarPayments:
    """The sync ``MyanmarPayments`` for this process, built from settings on first use.

    Gateways are created when first used and reused by every thread. Raises
    ``SynchronousOnlyOperation`` in async code: use :func:`get_async_payments`.
    """
    with _state.lock:
        if _state.sync is None:
            _state.sync = _build_sync(payments_settings(), _state.memory_cache)
        return _state.sync[0]


def get_async_payments() -> AsyncMyanmarPayments:
    """The ``AsyncMyanmarPayments`` for the running event loop, built on first use.

    Call it from async code (an async view under ASGI). Each event loop has its
    own facade and HTTP client; under ASGI that is one per worker process.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        raise RuntimeError(
            "get_async_payments() needs a running event loop: call it from async code, "
            "or use get_payments() in sync code."
        ) from None
    with _state.lock:
        entry = _state.by_loop.get(loop)
        if entry is None:
            entry = _build_async(payments_settings(), _state.memory_cache)
            _state.by_loop[loop] = entry
        return entry[0]


def close_payments() -> None:
    """Closes the sync facade's HTTP clients; the next :func:`get_payments` builds new ones.

    Closes the clients the gateways created and one from your ``http_client``
    factory, never a cache.
    """
    with _state.lock:
        entry, _state.sync = _state.sync, None
    if entry is not None:
        _close(entry)


async def aclose_payments() -> None:
    """Closes the running event loop's async facade, like :func:`close_payments`."""
    loop = asyncio.get_running_loop()
    with _state.lock:
        entry = _state.by_loop.pop(loop, None)
    if entry is not None:
        payments, client = entry
        await payments.aclose()
        if client is not None:
            await client.aclose()


def _close(entry: _SyncEntry) -> None:
    payments, client = entry
    payments.close()
    if client is not None:
        client.close()


def _client(conf: PaymentsSettings, option: str, client_class: type) -> Any:
    path = getattr(conf, option)
    if path is None:
        return None
    client = import_factory(option, path)()
    if not isinstance(client, client_class):
        raise ImproperlyConfigured(
            f'MYANMAR_PAYMENTS["{option}"] must return an {client_class.__module__}.'
            f"{client_class.__name__}."
        )
    return client


def _build_sync(conf: PaymentsSettings, memory_cache: MemoryTokenCache) -> _SyncEntry:
    token_cache = DjangoTokenCache(conf.token_cache) if conf.token_cache else memory_cache
    client: httpx.Client | None = _client(conf, "http_client", httpx.Client)
    if conf.from_env:
        payments = MyanmarPayments.from_env(token_cache=token_cache, http_client=client)
    else:
        payments = MyanmarPayments(**conf.gateways, token_cache=token_cache, http_client=client)
    return payments, client


def _build_async(conf: PaymentsSettings, memory_cache: MemoryTokenCache) -> _AsyncEntry:
    token_cache = AsyncDjangoTokenCache(conf.token_cache) if conf.token_cache else memory_cache
    client: httpx.AsyncClient | None = _client(conf, "async_http_client", httpx.AsyncClient)
    if conf.from_env:
        payments = AsyncMyanmarPayments.from_env(token_cache=token_cache, http_client=client)
    else:
        payments = AsyncMyanmarPayments(
            **conf.gateways, token_cache=token_cache, http_client=client
        )
    return payments, client


def _settings_changed(*, setting: str, **kwargs: Any) -> None:
    # override_settings and similar: rebuild from the new settings. Async facades are
    # dropped without aclose(), which needs their event loop.
    if setting in ("MYANMAR_PAYMENTS", "CACHES"):
        previous = _state.reset()
        if previous is not None:
            _close(previous)


setting_changed.connect(_settings_changed, dispatch_uid="django_myanmar_payments.clients")
