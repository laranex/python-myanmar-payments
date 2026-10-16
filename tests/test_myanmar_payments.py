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
    AsyncWavePay,
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
    WavePay,
    WavePayConfig,
    YomaMmqr,
    YomaMmqrConfig,
)
from tests.helpers import gateway_config

ENV = {
    "KBZ_PAY_APP_ID": "kp1",
    "KBZ_PAY_APP_KEY": "k",
    "KBZ_PAY_MERCHANT_CODE": "m",
    "KBZ_PAY_BASE_URL": "https://api.kbzpay.com/payment/gateway",
    "KBZ_PAY_PWA_BASE_REDIRECT_URL": "https://wap.kbzpay.com/pgw/pwa/#/",
    "KBZ_PAY_TIME_TO_LIVE_IN_MINUTES": "15",
    "WAVE_PAY_MERCHANT_ID": "w",
    "WAVE_PAY_SECRET_KEY": "s",
    "WAVE_PAY_MERCHANT_NAME": "Shop",
    "WAVE_PAY_TIME_TO_LIVE_IN_SECONDS": "300",
    "WAVE_PAY_BASE_URL": "https://payments.wavemoney.io",
    "WAVE_PAY_BASE_REDIRECT_URL": "https://payments.wavemoney.io",
    "AYA_PAY_APP_KEY": "a",
    "AYA_PAY_APP_SECRET": "s",
    "AYA_PAY_BASE_URL": "https://pgw.ayainnovation.com",
    "YOMA_MMQR_MERCHANT_ID": "y",
    "YOMA_MMQR_CLIENT_ID": "c",
    "YOMA_MMQR_CLIENT_SECRET": "s",
    "YOMA_MMQR_WEBHOOK_HASHKEY": "h",
    "YOMA_MMQR_API_VERSION": "v1rc",
    "YOMA_MMQR_WEBHOOK_SECRET": "w",
    "YOMA_MMQR_BASE_URL": "https://paymenthubapi.yomabank.com",
    "CYBER_SOURCE_PROFILE_ID": "p",
    "CYBER_SOURCE_ACCESS_KEY": "a",
    "CYBER_SOURCE_SECRET_KEY": "s",
    "CYBER_SOURCE_BASE_URL": "https://secureacceptance.cybersource.com",
    "MYANMAR_PAYMENTS_HTTP_TIMEOUT": "30",
}


class TestMyanmarPayments:
    def test_builds_each_gateway_once_from_the_environment(self) -> None:
        with MyanmarPayments.from_env(ENV) as payments:
            assert isinstance(payments.kbz_pay(), KbzPay)
            assert payments.kbz_pay() is payments.kbz_pay()
            assert payments.kbz_pay().config.app_id == "kp1"
            assert isinstance(payments.wave_pay(), WavePay)
            assert payments.wave_pay() is payments.wave_pay()
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
            kbz_pay=KbzPayConfig(**gateway_config("kbz_pay", app_id="a")),
            wave_pay=WavePayConfig(**gateway_config("wave_pay")),
            aya_pay=AyaPayConfig(**gateway_config("aya_pay")),
            yoma_mmqr=YomaMmqrConfig(**gateway_config("yoma_mmqr")),
            cyber_source=CyberSourceConfig(**gateway_config("cyber_source", profile_id="p")),
            token_cache=cache,
            http_client=client,
        )
        assert payments.kbz_pay().config.app_id == "a"
        assert payments.yoma_mmqr()._cache is cache
        assert payments.cyber_source().config.profile_id == "p"
        payments.close()
        assert not client.is_closed
        client.close()

    def test_takes_mappings_of_config_arguments(self) -> None:
        payments = MyanmarPayments(
            kbz_pay=gateway_config(
                "kbz_pay", app_id="a", timeout_seconds="30", time_to_live_minutes="15"
            ),
            wave_pay=gateway_config(
                "wave_pay", merchant_name="n", time_to_live_seconds="300", timeout_seconds=30
            ),
            aya_pay=gateway_config("aya_pay", app_key="k", timeout_seconds=30),
            yoma_mmqr=gateway_config("yoma_mmqr", webhook_hash_key="h", timeout_seconds=30),
            cyber_source=gateway_config("cyber_source", profile_id="p"),
        )
        assert payments.kbz_pay().config.app_id == "a"
        assert payments.kbz_pay().config.timeout_seconds == 30
        assert payments.kbz_pay().config.time_to_live_minutes == 15
        assert payments.wave_pay().config.merchant_name == "n"
        assert payments.aya_pay().config.app_key == "k"
        assert payments.yoma_mmqr().config.webhook_hash_key == "h"
        assert payments.cyber_source().config.profile_id == "p"
        payments.close()
        with pytest.raises(ConfigurationError) as info:
            MyanmarPayments(kbz_pay={"app_id": "a"}).kbz_pay()
        assert info.value.key == "app_key"
        with pytest.raises(TypeError):
            MyanmarPayments(kbz_pay={"unknown": "x"}).kbz_pay()

    def test_gateways_take_a_mapping_of_config_arguments(self) -> None:
        config = gateway_config("kbz_pay", merchant_code="c")
        for gateway in (KbzPay(config), AsyncKbzPay(config)):
            assert isinstance(gateway.config, KbzPayConfig)
            assert gateway.config.merchant_code == "c"
        cyber_source = CyberSource(gateway_config("cyber_source", base_url="https://cs.test/"))
        assert cyber_source.config.base_url == "https://cs.test"

    @pytest.mark.parametrize(
        ("method", "gateway", "key"),
        [
            ("kbz_pay", "kbz_pay", "app_id"),
            ("wave_pay", "wave_pay", "merchant_id"),
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
                assert isinstance(payments.wave_pay(), AsyncWavePay)
                assert payments.wave_pay() is payments.wave_pay()
                assert isinstance(payments.aya_pay(), AsyncAyaPay)
                assert payments.aya_pay() is payments.aya_pay()
                assert isinstance(payments.yoma_mmqr(), AsyncYomaMmqr)
                assert payments.yoma_mmqr() is payments.yoma_mmqr()
                assert isinstance(payments.cyber_source(), CyberSource)
            mapped = AsyncMyanmarPayments(
                aya_pay=gateway_config("aya_pay", app_secret="s", timeout_seconds=30)
            )
            assert mapped.aya_pay().config.app_secret == "s"
            await mapped.aclose()
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
            WavePay,
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
