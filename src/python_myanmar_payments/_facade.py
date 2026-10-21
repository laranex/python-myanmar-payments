"""Builds every gateway from one configuration (or the environment)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import TracebackType
from typing import Any, TypeVar

import httpx

from ._cache import AsyncTokenCache, MemoryTokenCache, TokenCache
from ._errors import ConfigurationError
from ._support import EnvSource, config_of
from .aya_pay import AsyncAyaPay, AyaPay, AyaPayConfig
from .cyber_source import CyberSource, CyberSourceConfig
from .kbz_pay import AsyncKbzPay, KbzPay, KbzPayConfig
from .wave_pay import AsyncWavePay, WavePay, WavePayConfig
from .wave_pay_mini_app import AsyncWavePayMiniApp, WavePayMiniApp, WavePayMiniAppConfig
from .yoma_mmqr import AsyncYomaMmqr, YomaMmqr, YomaMmqrConfig

__all__ = ["AsyncMyanmarPayments", "MyanmarPayments"]

_C = TypeVar("_C")


def _lazy(config_class: type[_C], config: _C | Mapping[str, Any] | None) -> Callable[[], _C] | None:
    return None if config is None else lambda: config_of(config_class, config)


def _resolve(source: Callable[[], _C] | None, gateway: str, first_key: str) -> _C:
    if source is None:
        raise ConfigurationError(gateway, first_key)
    return source()


class _Payments:
    """Holds each gateway's configuration until the gateway is first used."""

    def __init__(
        self,
        kbz_pay: KbzPayConfig | Mapping[str, Any] | None,
        wave_pay: WavePayConfig | Mapping[str, Any] | None,
        wave_pay_mini_app: WavePayMiniAppConfig | Mapping[str, Any] | None,
        aya_pay: AyaPayConfig | Mapping[str, Any] | None,
        yoma_mmqr: YomaMmqrConfig | Mapping[str, Any] | None,
        cyber_source: CyberSourceConfig | Mapping[str, Any] | None,
    ) -> None:
        self._kbz_pay_config = _lazy(KbzPayConfig, kbz_pay)
        self._wave_pay_config = _lazy(WavePayConfig, wave_pay)
        self._wave_pay_mini_app_config = _lazy(WavePayMiniAppConfig, wave_pay_mini_app)
        self._aya_pay_config = _lazy(AyaPayConfig, aya_pay)
        self._yoma_mmqr_config = _lazy(YomaMmqrConfig, yoma_mmqr)
        self._cyber_source_config = _lazy(CyberSourceConfig, cyber_source)
        self._cyber_source: CyberSource | None = None

    def _read_env(self, env: EnvSource | None) -> None:
        self._kbz_pay_config = lambda: KbzPayConfig.from_env(env)
        self._wave_pay_config = lambda: WavePayConfig.from_env(env)
        self._wave_pay_mini_app_config = lambda: WavePayMiniAppConfig.from_env(env)
        self._aya_pay_config = lambda: AyaPayConfig.from_env(env)
        self._yoma_mmqr_config = lambda: YomaMmqrConfig.from_env(env)
        self._cyber_source_config = lambda: CyberSourceConfig.from_env(env)

    def cyber_source(self) -> CyberSource:
        """The CyberSource gateway. Raises a ``ConfigurationError`` when not configured."""
        if self._cyber_source is None:
            config = _resolve(self._cyber_source_config, "cyber_source", "profile_id")
            self._cyber_source = CyberSource(config)
        return self._cyber_source


class MyanmarPayments(_Payments):
    """Builds each gateway (sync clients) from one configuration or the environment.

    Gateways are created on first use and reused, so only the gateways you call
    need to be configured. Create it once and share it; :meth:`close` (or a
    ``with`` block) closes the HTTP clients it created.
    """

    def __init__(
        self,
        *,
        kbz_pay: KbzPayConfig | Mapping[str, Any] | None = None,
        wave_pay: WavePayConfig | Mapping[str, Any] | None = None,
        wave_pay_mini_app: WavePayMiniAppConfig | Mapping[str, Any] | None = None,
        aya_pay: AyaPayConfig | Mapping[str, Any] | None = None,
        yoma_mmqr: YomaMmqrConfig | Mapping[str, Any] | None = None,
        cyber_source: CyberSourceConfig | Mapping[str, Any] | None = None,
        token_cache: TokenCache | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        super().__init__(kbz_pay, wave_pay, wave_pay_mini_app, aya_pay, yoma_mmqr, cyber_source)
        self._http_client = http_client
        self._token_cache: TokenCache = (
            token_cache if token_cache is not None else MemoryTokenCache()
        )
        self._kbz_pay: KbzPay | None = None
        self._wave_pay: WavePay | None = None
        self._wave_pay_mini_app: WavePayMiniApp | None = None
        self._aya_pay: AyaPay | None = None
        self._yoma_mmqr: YomaMmqr | None = None

    @classmethod
    def from_env(
        cls,
        env: EnvSource | None = None,
        *,
        token_cache: TokenCache | None = None,
        http_client: httpx.Client | None = None,
    ) -> MyanmarPayments:
        """Reads every gateway's configuration from environment variables.

        ``KBZ_PAY_*``, ``WAVE_PAY_*``, ``WAVE_PAY_MINI_APP_*``, ``AYA_PAY_*``,
        ``YOMA_MMQR_*``, ``CYBER_SOURCE_*`` and ``MYANMAR_PAYMENTS_HTTP_TIMEOUT`` are read when the
        gateway is first used. Defaults to ``os.environ``.
        """
        payments = cls(token_cache=token_cache, http_client=http_client)
        payments._read_env(env)
        return payments

    def kbz_pay(self) -> KbzPay:
        """The KBZ Pay gateway. Raises a ``ConfigurationError`` when not configured."""
        if self._kbz_pay is None:
            config = _resolve(self._kbz_pay_config, "kbz_pay", "app_id")
            self._kbz_pay = KbzPay(config, http_client=self._http_client)
        return self._kbz_pay

    def wave_pay(self) -> WavePay:
        """The Wave Pay gateway. Raises a ``ConfigurationError`` when not configured."""
        if self._wave_pay is None:
            config = _resolve(self._wave_pay_config, "wave_pay", "merchant_id")
            self._wave_pay = WavePay(config, http_client=self._http_client)
        return self._wave_pay

    def wave_pay_mini_app(self) -> WavePayMiniApp:
        """The Wave Pay Mini App gateway. Raises a ``ConfigurationError`` when not configured."""
        if self._wave_pay_mini_app is None:
            config = _resolve(self._wave_pay_mini_app_config, "wave_pay_mini_app", "client_id")
            self._wave_pay_mini_app = WavePayMiniApp(
                config,
                token_cache=self._token_cache,
                http_client=self._http_client,
            )
        return self._wave_pay_mini_app

    def aya_pay(self) -> AyaPay:
        """The AYA Payment Gateway. Raises a ``ConfigurationError`` when not configured."""
        if self._aya_pay is None:
            config = _resolve(self._aya_pay_config, "aya_pay", "app_key")
            self._aya_pay = AyaPay(config, http_client=self._http_client)
        return self._aya_pay

    def yoma_mmqr(self) -> YomaMmqr:
        """The Yoma MMQR gateway. Raises a ``ConfigurationError`` when not configured."""
        if self._yoma_mmqr is None:
            config = _resolve(self._yoma_mmqr_config, "yoma_mmqr", "merchant_id")
            self._yoma_mmqr = YomaMmqr(
                config,
                token_cache=self._token_cache,
                http_client=self._http_client,
            )
        return self._yoma_mmqr

    def close(self) -> None:
        """Closes the HTTP clients the gateways created. A client you passed stays open."""
        for gateway in (
            self._kbz_pay,
            self._wave_pay,
            self._wave_pay_mini_app,
            self._aya_pay,
            self._yoma_mmqr,
        ):
            if gateway is not None:
                gateway.close()

    def __enter__(self) -> MyanmarPayments:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class AsyncMyanmarPayments(_Payments):
    """Builds each gateway (async clients) from one configuration or the environment.

    The async twin of :class:`MyanmarPayments`: ``kbz_pay()`` returns an
    :class:`AsyncKbzPay` and so on. :meth:`aclose` (or an ``async with`` block)
    closes the HTTP clients it created.
    """

    def __init__(
        self,
        *,
        kbz_pay: KbzPayConfig | Mapping[str, Any] | None = None,
        wave_pay: WavePayConfig | Mapping[str, Any] | None = None,
        wave_pay_mini_app: WavePayMiniAppConfig | Mapping[str, Any] | None = None,
        aya_pay: AyaPayConfig | Mapping[str, Any] | None = None,
        yoma_mmqr: YomaMmqrConfig | Mapping[str, Any] | None = None,
        cyber_source: CyberSourceConfig | Mapping[str, Any] | None = None,
        token_cache: TokenCache | AsyncTokenCache | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(kbz_pay, wave_pay, wave_pay_mini_app, aya_pay, yoma_mmqr, cyber_source)
        self._http_client = http_client
        self._token_cache: TokenCache | AsyncTokenCache = (
            token_cache if token_cache is not None else MemoryTokenCache()
        )
        self._kbz_pay: AsyncKbzPay | None = None
        self._wave_pay: AsyncWavePay | None = None
        self._wave_pay_mini_app: AsyncWavePayMiniApp | None = None
        self._aya_pay: AsyncAyaPay | None = None
        self._yoma_mmqr: AsyncYomaMmqr | None = None

    @classmethod
    def from_env(
        cls,
        env: EnvSource | None = None,
        *,
        token_cache: TokenCache | AsyncTokenCache | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> AsyncMyanmarPayments:
        """Reads every gateway's configuration from environment variables on first use."""
        payments = cls(token_cache=token_cache, http_client=http_client)
        payments._read_env(env)
        return payments

    def kbz_pay(self) -> AsyncKbzPay:
        """The KBZ Pay gateway. Raises a ``ConfigurationError`` when not configured."""
        if self._kbz_pay is None:
            config = _resolve(self._kbz_pay_config, "kbz_pay", "app_id")
            self._kbz_pay = AsyncKbzPay(config, http_client=self._http_client)
        return self._kbz_pay

    def wave_pay(self) -> AsyncWavePay:
        """The Wave Pay gateway. Raises a ``ConfigurationError`` when not configured."""
        if self._wave_pay is None:
            config = _resolve(self._wave_pay_config, "wave_pay", "merchant_id")
            self._wave_pay = AsyncWavePay(config, http_client=self._http_client)
        return self._wave_pay

    def wave_pay_mini_app(self) -> AsyncWavePayMiniApp:
        """The Wave Pay Mini App gateway. Raises a ``ConfigurationError`` when not configured."""
        if self._wave_pay_mini_app is None:
            config = _resolve(self._wave_pay_mini_app_config, "wave_pay_mini_app", "client_id")
            self._wave_pay_mini_app = AsyncWavePayMiniApp(
                config,
                token_cache=self._token_cache,
                http_client=self._http_client,
            )
        return self._wave_pay_mini_app

    def aya_pay(self) -> AsyncAyaPay:
        """The AYA Payment Gateway. Raises a ``ConfigurationError`` when not configured."""
        if self._aya_pay is None:
            config = _resolve(self._aya_pay_config, "aya_pay", "app_key")
            self._aya_pay = AsyncAyaPay(config, http_client=self._http_client)
        return self._aya_pay

    def yoma_mmqr(self) -> AsyncYomaMmqr:
        """The Yoma MMQR gateway. Raises a ``ConfigurationError`` when not configured."""
        if self._yoma_mmqr is None:
            config = _resolve(self._yoma_mmqr_config, "yoma_mmqr", "merchant_id")
            self._yoma_mmqr = AsyncYomaMmqr(
                config,
                token_cache=self._token_cache,
                http_client=self._http_client,
            )
        return self._yoma_mmqr

    async def aclose(self) -> None:
        """Closes the HTTP clients the gateways created. A client you passed stays open."""
        for gateway in (
            self._kbz_pay,
            self._wave_pay,
            self._wave_pay_mini_app,
            self._aya_pay,
            self._yoma_mmqr,
        ):
            if gateway is not None:
                await gateway.aclose()

    async def __aenter__(self) -> AsyncMyanmarPayments:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()
