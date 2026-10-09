from __future__ import annotations

import asyncio
import re
from pathlib import Path

import httpx
import pytest

import python_myanmar_payments as api
from python_myanmar_payments import (
    AsyncAyaPay,
    AsyncKbzPay,
    AsyncMyanmarPayments,
    AsyncWaveMoney,
    AsyncYomaMmqr,
    AyaPay,
    AyaPayConfig,
    ConfigurationError,
    CyberSource,
    CyberSourceConfig,
    KbzPay,
    KbzPayConfig,
    MemoryTokenCache,
    MyanmarPayments,
    WaveMoney,
    WaveMoneyConfig,
    YomaMmqr,
    YomaMmqrConfig,
)

ENV = {
    "KBZ_PAY_APP_ID": "kp1",
    "KBZ_PAY_APP_KEY": "k",
    "KBZ_PAY_MERCHANT_CODE": "m",
    "WAVE_MONEY_MERCHANT_ID": "w",
    "WAVE_MONEY_SECRET_KEY": "s",
    "WAVE_MONEY_MERCHANT_NAME": "Shop",
    "AYA_PAY_APP_KEY": "a",
    "AYA_PAY_APP_SECRET": "s",
    "YOMA_MMQR_MERCHANT_ID": "y",
    "YOMA_MMQR_CLIENT_ID": "c",
    "YOMA_MMQR_CLIENT_SECRET": "s",
    "YOMA_MMQR_WEBHOOK_HASHKEY": "h",
    "CYBER_SOURCE_PROFILE_ID": "p",
    "CYBER_SOURCE_ACCESS_KEY": "a",
    "CYBER_SOURCE_SECRET_KEY": "s",
}


class TestMyanmarPayments:
    def test_builds_each_gateway_once_from_the_environment(self) -> None:
        with MyanmarPayments.from_env(ENV) as payments:
            assert isinstance(payments.kbz_pay(), KbzPay)
            assert payments.kbz_pay() is payments.kbz_pay()
            assert payments.kbz_pay().config.app_id == "kp1"
            assert isinstance(payments.wave_money(), WaveMoney)
            assert payments.wave_money() is payments.wave_money()
            assert isinstance(payments.aya_pay(), AyaPay)
            assert payments.aya_pay() is payments.aya_pay()
            assert isinstance(payments.yoma_mmqr(), YomaMmqr)
            assert payments.yoma_mmqr() is payments.yoma_mmqr()
            assert isinstance(payments.cyber_source(), CyberSource)
            assert payments.cyber_source() is payments.cyber_source()

    def test_reads_only_the_gateways_you_use(self) -> None:
        payments = MyanmarPayments.from_env({"KBZ_PAY_APP_ID": "x"})
        with pytest.raises(ConfigurationError) as info:
            payments.kbz_pay()
        assert info.value.key == "app_key"
        with pytest.raises(ConfigurationError):
            payments.cyber_source()

    def test_reads_os_environ_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key, value in ENV.items():
            monkeypatch.setenv(key, value)
        assert MyanmarPayments.from_env().kbz_pay().config.app_id == "kp1"

    def test_takes_config_objects_and_shares_the_client_and_cache(self) -> None:
        cache = MemoryTokenCache()
        client = httpx.Client()
        payments = MyanmarPayments(
            kbz_pay=KbzPayConfig(app_id="a", app_key="b", merchant_code="c"),
            wave_money=WaveMoneyConfig(merchant_id="m", secret_key="s", merchant_name="n"),
            aya_pay=AyaPayConfig(app_key="k", app_secret="s"),
            yoma_mmqr=YomaMmqrConfig(
                merchant_id="m", client_id="c", client_secret="s", webhook_hash_key="h"
            ),
            cyber_source=CyberSourceConfig(profile_id="p", access_key="a", secret_key="s"),
            token_cache=cache,
            http_client=client,
        )
        assert payments.kbz_pay().config.app_id == "a"
        assert payments.yoma_mmqr()._cache is cache
        assert payments.cyber_source().config.profile_id == "p"
        payments.close()
        assert not client.is_closed
        client.close()

    @pytest.mark.parametrize(
        ("method", "gateway", "key"),
        [
            ("kbz_pay", "kbz_pay", "app_id"),
            ("wave_money", "wave_money", "merchant_id"),
            ("aya_pay", "aya_pay", "app_key"),
            ("yoma_mmqr", "yoma_mmqr", "merchant_id"),
            ("cyber_source", "cyber_source", "profile_id"),
        ],
    )
    def test_names_an_unconfigured_gateway(self, method: str, gateway: str, key: str) -> None:
        for payments in (MyanmarPayments(), AsyncMyanmarPayments()):
            with pytest.raises(ConfigurationError) as info:
                getattr(payments, method)()
            assert (info.value.gateway, info.value.key) == (gateway, key)


class TestAsyncMyanmarPayments:
    def test_builds_async_gateways(self) -> None:
        async def scenario() -> None:
            async with AsyncMyanmarPayments.from_env(ENV) as payments:
                assert isinstance(payments.kbz_pay(), AsyncKbzPay)
                assert payments.kbz_pay() is payments.kbz_pay()
                assert isinstance(payments.wave_money(), AsyncWaveMoney)
                assert payments.wave_money() is payments.wave_money()
                assert isinstance(payments.aya_pay(), AsyncAyaPay)
                assert payments.aya_pay() is payments.aya_pay()
                assert isinstance(payments.yoma_mmqr(), AsyncYomaMmqr)
                assert payments.yoma_mmqr() is payments.yoma_mmqr()
                assert isinstance(payments.cyber_source(), CyberSource)
            empty = AsyncMyanmarPayments()
            await empty.aclose()

        asyncio.run(scenario())


SKILL = Path(__file__).parent.parent / "skills" / "python-myanmar-payments" / "SKILL.md"


class TestAgentSkill:
    def test_only_names_api_that_exists(self) -> None:
        skill = SKILL.read_text("utf-8")
        assert skill.startswith("---\nname: python-myanmar-payments\n")
        # Names after a dot belong to other libraries, e.g. httpx.AsyncClient.
        pattern = r"(?<![.\w])(?:Async)?[A-Z][a-z]+(?:[A-Z][a-z]+|Mmqr|Qr)+\b"
        names = set(re.findall(pattern, skill))
        names |= set(re.findall(r"\b[A-Z][a-z]+Error\b", skill))
        names -= {"FastAPI", "HttpResponse"}  # Django's response class
        assert len(names) > 20
        for name in sorted(names):
            assert hasattr(api, name), name

    def test_only_names_methods_that_exist(self) -> None:
        skill = SKILL.read_text("utf-8")
        gateways = [
            KbzPay,
            AsyncKbzPay,
            WaveMoney,
            AyaPay,
            YomaMmqr,
            CyberSource,
            MyanmarPayments,
            api.CallbackRequest,
            api.PaymentCallback,
            api.FormPayment,
            api.QrPayment,
            api.AppPayment,
            api.Amount,
            api.PaymentStatus,
            api.PaymentStatusResult,
            api.AyaPayService,
        ]
        framework = {"get_data"}  # Flask's request.get_data()
        for method in sorted(set(re.findall(r"\.([a-z_]+)\(", skill)) - framework):
            assert any(hasattr(cls, method) for cls in gateways), method
