from __future__ import annotations

import asyncio
import threading

import pytest
from django.core.cache import caches
from django.core.exceptions import ImproperlyConfigured, SynchronousOnlyOperation
from django.test import override_settings
from python_myanmar_payments import (
    AsyncKbzPay,
    AsyncMyanmarPayments,
    ConfigurationError,
    KbzPay,
    MyanmarPayments,
)

from django_myanmar_payments import (
    AsyncDjangoTokenCache,
    DjangoTokenCache,
    aclose_payments,
    close_payments,
    get_async_payments,
    get_payments,
)
from tests.helpers import CONFIG, HTTP_CLIENTS, gateway
from tests.urls import wave_data

pytestmark = pytest.mark.anyio

WITH_CLIENTS = {**CONFIG, **HTTP_CLIENTS}
STATUS_PATH = "/payment-gateway/v1rc/api/payment/check-status"


class TestSyncPayments:
    def test_builds_one_facade_from_settings_on_first_use(self) -> None:
        payments = get_payments()
        assert isinstance(payments, MyanmarPayments)
        assert payments is get_payments()
        assert isinstance(payments.kbz_pay(), KbzPay)
        assert payments.kbz_pay() is get_payments().kbz_pay()
        assert payments.kbz_pay().config.app_id == CONFIG["kbz_pay"]["app_id"]

    def test_is_shared_by_threads(self) -> None:
        seen: list[MyanmarPayments] = []
        threads = [threading.Thread(target=lambda: seen.append(get_payments())) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len({id(payments) for payments in seen}) == 1

    def test_reads_only_the_gateways_you_use_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KBZ_PAY_APP_ID", "from-env")
        monkeypatch.setenv("KBZ_PAY_APP_KEY", "key")
        monkeypatch.setenv("KBZ_PAY_MERCHANT_CODE", "100001")
        monkeypatch.setenv("KBZ_PAY_BASE_URL", "https://api.kbzpay.com/payment/gateway")
        monkeypatch.setenv("KBZ_PAY_PWA_BASE_REDIRECT_URL", "https://wap.kbzpay.com/pgw/pwa/#/")
        monkeypatch.setenv("KBZ_PAY_TIME_TO_LIVE_IN_MINUTES", "15")
        monkeypatch.delenv("WAVE_PAY_MERCHANT_ID", raising=False)
        with override_settings(MYANMAR_PAYMENTS={}):
            assert get_payments().kbz_pay().config.app_id == "from-env"
            with pytest.raises(ConfigurationError, match="merchant_id"):
                get_payments().wave_pay()

    def test_an_unconfigured_gateway_raises_the_core_error(self) -> None:
        with (
            override_settings(MYANMAR_PAYMENTS={"wave_pay": CONFIG["wave_pay"]}),
            pytest.raises(ConfigurationError, match="app_id"),
        ):
            get_payments().kbz_pay()

    def test_settings_changes_rebuild_and_close_the_old_facade(self) -> None:
        with override_settings(MYANMAR_PAYMENTS=WITH_CLIENTS):
            first = get_payments()
            [client] = gateway.clients[-1:]
            with override_settings(MYANMAR_PAYMENTS={**WITH_CLIENTS, "kbz_pay": CONFIG["kbz_pay"]}):
                assert client.is_closed
                assert get_payments() is not first

    def test_close_payments_closes_the_client_it_built(self) -> None:
        with override_settings(MYANMAR_PAYMENTS=WITH_CLIENTS):
            assert get_payments().wave_pay() is not None
            client = gateway.clients[-1]
            close_payments()
            assert client.is_closed
            close_payments()
            assert get_payments().wave_pay() is not None
            assert gateway.clients[-1] is not client

    def test_rejects_a_factory_returning_something_else(self) -> None:
        value = {**CONFIG, "http_client": "tests.helpers.async_client"}
        with (
            override_settings(MYANMAR_PAYMENTS=value),
            pytest.raises(ImproperlyConfigured, match=r"must return an httpx.Client"),
        ):
            get_payments()

    def test_other_settings_changes_keep_the_facade(self) -> None:
        payments = get_payments()
        with override_settings(USE_TZ=False):
            assert get_payments() is payments

    def test_refuses_to_run_in_async_code(self) -> None:
        async def call() -> None:
            get_payments()

        with pytest.raises(SynchronousOnlyOperation):
            asyncio.run(call())


class TestAsyncPayments:
    async def test_builds_one_facade_per_event_loop(self) -> None:
        payments = get_async_payments()
        assert isinstance(payments, AsyncMyanmarPayments)
        assert payments is get_async_payments()
        assert isinstance(payments.kbz_pay(), AsyncKbzPay)

        async def other_loop() -> AsyncMyanmarPayments:
            return get_async_payments()

        elsewhere = await asyncio.to_thread(asyncio.run, other_loop())
        assert elsewhere is not payments

    async def test_reads_the_environment_and_closes_without_a_factory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CYBER_SOURCE_PROFILE_ID", "from-env")
        monkeypatch.setenv("CYBER_SOURCE_ACCESS_KEY", "access")
        monkeypatch.setenv("CYBER_SOURCE_SECRET_KEY", "secret")
        monkeypatch.setenv("CYBER_SOURCE_BASE_URL", "https://secureacceptance.cybersource.com")
        with override_settings(MYANMAR_PAYMENTS={}):
            assert get_async_payments().cyber_source().config.profile_id == "from-env"
            await aclose_payments()

    def test_needs_a_running_event_loop(self) -> None:
        with pytest.raises(RuntimeError, match=r"needs a running event loop"):
            get_async_payments()

    async def test_aclose_payments_closes_this_loops_client(self) -> None:
        with override_settings(MYANMAR_PAYMENTS=WITH_CLIENTS):
            payment = await get_async_payments().wave_pay().initiate(wave_data())
            assert payment.url.endswith("transaction_id=T1")
            client = gateway.clients[-1]
            await aclose_payments()
            assert client.is_closed
            await aclose_payments()
            assert get_async_payments() is not None
            assert gateway.clients[-1] is not client
            await aclose_payments()

    async def test_rejects_a_sync_client_factory(self) -> None:
        value = {**CONFIG, "async_http_client": "tests.helpers.sync_client"}
        with (
            override_settings(MYANMAR_PAYMENTS=value),
            pytest.raises(ImproperlyConfigured, match=r"must return an httpx.AsyncClient"),
        ):
            get_async_payments()


class TestTokenCaches:
    async def test_sync_and_async_facades_share_one_yoma_token(self) -> None:
        with override_settings(MYANMAR_PAYMENTS=WITH_CLIENTS):
            await asyncio.to_thread(lambda: get_payments().yoma_mmqr().status("REF1"))
            await get_async_payments().yoma_mmqr().status("REF1")
            await aclose_payments()
            await asyncio.to_thread(close_payments)
        assert gateway.paths == ["/token", STATUS_PATH, STATUS_PATH]

    async def test_a_django_cache_keeps_the_token_across_facades(self) -> None:
        with override_settings(MYANMAR_PAYMENTS={**WITH_CLIENTS, "token_cache": "tokens"}):
            for _ in range(2):
                await asyncio.to_thread(lambda: get_payments().yoma_mmqr().status("REF1"))
                await asyncio.to_thread(close_payments)
                await get_async_payments().yoma_mmqr().status("REF1")
                await aclose_payments()
        assert gateway.paths.count("/token") == 1
        assert gateway.paths.count(STATUS_PATH) == 4

    def test_django_token_cache(self) -> None:
        cache = DjangoTokenCache("tokens")
        cache.set("forever", "a", 0)
        cache.set("hour", "b", 3600)
        caches["tokens"].set("number", 1)
        assert (cache.get("forever"), cache.get("hour"), cache.get("number")) == ("a", "b", None)
        cache.set("gone", "c", -1)
        cache.delete("hour")
        assert (cache.get("gone"), cache.get("hour"), cache.get("missing")) == ("c", None, None)
        assert DjangoTokenCache().alias == "default"

    async def test_async_django_token_cache(self) -> None:
        cache = AsyncDjangoTokenCache("tokens")
        await cache.set("forever", "a", 0)
        await cache.set("hour", "b", 3600)
        await caches["tokens"].aset("number", 1)
        assert await cache.get("forever") == "a"
        assert await cache.get("hour") == "b"
        assert await cache.get("number") is None
        await cache.delete("hour")
        assert await cache.get("hour") is None
        assert AsyncDjangoTokenCache().alias == "default"
