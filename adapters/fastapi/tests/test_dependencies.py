from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from python_myanmar_payments import (
    AsyncAyaPay,
    AsyncKbzPay,
    AsyncMyanmarPayments,
    AsyncWavePay,
    AsyncYomaMmqr,
    ConfigurationError,
    CyberSource,
    WavePayItem,
    WavePayPaymentData,
)

from fastapi_myanmar_payments import (
    AyaPayDep,
    CyberSourceDep,
    KbzPayDep,
    PaymentsDep,
    WavePayDep,
    YomaMmqrDep,
    get_kbz_pay,
    get_payments,
    payments_lifespan,
)
from tests.helpers import CONFIG, SpyPayments, payments, serve

pytestmark = pytest.mark.anyio


def gateway_app(**options: Any) -> FastAPI:
    app = FastAPI(**options)

    @app.get("/gateways")
    async def gateways(
        facade: PaymentsDep,
        kbz: KbzPayDep,
        wave: WavePayDep,
        aya: AyaPayDep,
        yoma: YomaMmqrDep,
        cyber: CyberSourceDep,
    ) -> dict[str, Any]:
        return {
            "facade": id(facade),
            "kbz": [type(kbz).__name__, kbz is facade.kbz_pay()],
            "wave": [type(wave).__name__, wave is facade.wave_pay()],
            "aya": [type(aya).__name__, aya is facade.aya_pay()],
            "yoma": [type(yoma).__name__, yoma is facade.yoma_mmqr()],
            "cyber": [type(cyber).__name__, cyber is facade.cyber_source()],
        }

    return app


class TestLifespan:
    async def test_creates_one_facade_and_closes_it_at_shutdown(self) -> None:
        created: list[SpyPayments] = []

        def factory() -> SpyPayments:
            created.append(payments())
            return created[-1]

        app = gateway_app(lifespan=payments_lifespan(factory))
        async with serve(app) as client:
            first = (await client.get("/gateways")).json()
            second = (await client.get("/gateways")).json()
            assert len(created) == 1
            assert created[0].closed == 0
            assert app.state.myanmar_payments is created[0]

        assert first == second
        assert first["facade"] == id(created[0])
        assert first["kbz"] == [AsyncKbzPay.__name__, True]
        assert first["wave"] == [AsyncWavePay.__name__, True]
        assert first["aya"] == [AsyncAyaPay.__name__, True]
        assert first["yoma"] == [AsyncYomaMmqr.__name__, True]
        assert first["cyber"] == [CyberSource.__name__, True]
        assert created[0].closed == 1
        assert not hasattr(app.state, "myanmar_payments")

    async def test_never_closes_a_facade_you_pass(self) -> None:
        facade = payments()
        async with serve(gateway_app(lifespan=payments_lifespan(facade))) as client:
            assert (await client.get("/gateways")).json()["facade"] == id(facade)
        assert facade.closed == 0

    async def test_closes_a_facade_it_created_but_not_your_http_client(self) -> None:
        sent: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(request)
            return httpx.Response(200, json={"message": "success", "transaction_id": "T1"})

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        created: list[SpyPayments] = []

        def factory() -> SpyPayments:
            created.append(payments(http_client=http_client))
            return created[-1]

        app = FastAPI(lifespan=payments_lifespan(factory))

        @app.post("/pay")
        async def pay(wave: WavePayDep) -> dict[str, str]:
            payment = await wave.initiate(
                WavePayPaymentData(
                    order_id="100",
                    callback_url="https://shop.test/wave/callback",
                    return_url="https://shop.test/done",
                    description="Order 100",
                    items=[WavePayItem("Shoes", 1000)],
                )
            )
            return {"url": payment.url}

        async with serve(app) as client:
            for _ in range(2):
                assert (await client.post("/pay")).json() == {
                    "url": "https://payments.wavemoney.io/authenticate?transaction_id=T1"
                }
        assert [str(request.url) for request in sent] == [
            "https://payments.wavemoney.io/payment"
        ] * 2
        assert created[0].closed == 1
        assert not http_client.is_closed
        await http_client.aclose()

    async def test_composes_with_your_own_lifespan(self) -> None:
        facade = payments()
        events: list[str] = []
        inner = payments_lifespan(facade)

        @asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncIterator[None]:
            async with inner(app):
                events.append("started")
                yield
            events.append("stopped")

        async with serve(gateway_app(lifespan=lifespan)) as client:
            assert (await client.get("/gateways")).json()["facade"] == id(facade)
        assert events == ["started", "stopped"]


class TestDependencies:
    async def test_explains_a_missing_lifespan(self) -> None:
        async with serve(gateway_app()) as client:
            with pytest.raises(RuntimeError, match=r"pass payments_lifespan\(\.\.\.\)"):
                await client.get("/gateways")

    async def test_reports_an_unconfigured_gateway_with_the_core_error(self) -> None:
        app = FastAPI(lifespan=payments_lifespan(AsyncMyanmarPayments(wave_pay=CONFIG["wave_pay"])))

        @app.get("/kbz")
        async def kbz(gateway: KbzPayDep) -> None:
            raise AssertionError("unreachable")  # pragma: no cover

        async with serve(app) as client:
            with pytest.raises(ConfigurationError, match="app_id"):
                await client.get("/kbz")

    async def test_overriding_get_payments_feeds_every_gateway(self) -> None:
        facade = payments()
        app = gateway_app()
        app.dependency_overrides[get_payments] = lambda: facade
        async with serve(app) as client:
            body = (await client.get("/gateways")).json()
        assert body["facade"] == id(facade)
        assert body["kbz"] == [AsyncKbzPay.__name__, True]

    async def test_a_gateway_can_be_overridden_on_its_own(self) -> None:
        class FakeKbz:
            async def status(self, order_id: str) -> str:
                return f"paid {order_id}"

        app = FastAPI()

        @app.get("/status/{order_id}")
        async def status(order_id: str, kbz: KbzPayDep) -> Any:
            return await kbz.status(order_id)

        app.dependency_overrides[get_kbz_pay] = FakeKbz
        async with serve(app) as client:
            assert (await client.get("/status/ORDER_1")).json() == "paid ORDER_1"
