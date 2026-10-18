from __future__ import annotations

from typing import Any

import pytest
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import SystemCheckError
from django.test import override_settings
from django.views.debug import SafeExceptionReporterFilter
from python_myanmar_payments import KbzPayConfig

from django_myanmar_payments.apps import check_settings
from django_myanmar_payments.conf import import_factory, payments_settings
from tests.helpers import CONFIG

SECRET = "do-not-print-me"


class TestPaymentsSettings:
    def test_reads_explicit_gateways_and_options(self) -> None:
        kbz = KbzPayConfig(**CONFIG["kbz_pay"])
        value = {"kbz_pay": kbz, "wave_pay": CONFIG["wave_pay"], "token_cache": "tokens"}
        with override_settings(MYANMAR_PAYMENTS=value):
            conf = payments_settings()
        assert conf.gateways == {"kbz_pay": kbz, "wave_pay": CONFIG["wave_pay"]}
        assert conf.token_cache == "tokens"
        assert conf.http_client is None
        assert not conf.from_env

    def test_reads_the_environment_without_gateway_keys(self) -> None:
        with override_settings(MYANMAR_PAYMENTS={"token_cache": "default"}):
            assert payments_settings().from_env
        with override_settings():
            del settings.MYANMAR_PAYMENTS
            assert payments_settings().from_env

    @pytest.mark.parametrize(
        ("value", "message"),
        [
            ([("kbz_pay", {})], "MYANMAR_PAYMENTS must be a dict."),
            (
                {"kbz": {"app_key": SECRET}, "TOKEN_CACHE": SECRET},
                "MYANMAR_PAYMENTS has unknown keys: TOKEN_CACHE, kbz. Allowed keys: kbz_pay, "
                "wave_pay, aya_pay, yoma_mmqr, cyber_source, token_cache, http_client, "
                "async_http_client.",
            ),
            ({"kbz_pay": SECRET}, 'MYANMAR_PAYMENTS["kbz_pay"] must be a dict or a KbzPayConfig.'),
            (
                {"wave_pay": KbzPayConfig(**CONFIG["kbz_pay"])},
                'MYANMAR_PAYMENTS["wave_pay"] must be a dict or a WavePayConfig.',
            ),
            ({"http_client": ""}, 'MYANMAR_PAYMENTS["http_client"] must be a non-empty string.'),
            ({"token_cache": 1}, 'MYANMAR_PAYMENTS["token_cache"] must be a non-empty string.'),
            (
                {"token_cache": "redis"},
                'MYANMAR_PAYMENTS["token_cache"] is "redis", which is not a CACHES alias.',
            ),
        ],
    )
    def test_explains_invalid_settings_without_values(self, value: Any, message: str) -> None:
        with override_settings(MYANMAR_PAYMENTS=value), pytest.raises(ImproperlyConfigured) as info:
            payments_settings()
        assert str(info.value) == message
        assert SECRET not in str(info.value)

    def test_imports_factories(self) -> None:
        assert callable(import_factory("http_client", "tests.helpers.sync_client"))
        with pytest.raises(ImproperlyConfigured, match=r'"tests.nowhere", which cannot be'):
            import_factory("http_client", "tests.nowhere")
        with pytest.raises(ImproperlyConfigured, match=r'"tests.helpers.FIXTURES", not a function'):
            import_factory("async_http_client", "tests.helpers.FIXTURES")

    def test_debug_pages_hide_credentials(self) -> None:
        safe = SafeExceptionReporterFilter().get_safe_settings()["MYANMAR_PAYMENTS"]
        for gateway, secret in [
            ("kbz_pay", "app_key"),
            ("wave_pay", "secret_key"),
            ("aya_pay", "app_secret"),
            ("yoma_mmqr", "client_secret"),
            ("yoma_mmqr", "webhook_secret"),
            ("yoma_mmqr", "webhook_hash_key"),
            ("cyber_source", "secret_key"),
            ("cyber_source", "access_key"),
        ]:
            assert safe[gateway][secret] == "********************", (gateway, secret)
        assert safe["kbz_pay"]["app_id"] == CONFIG["kbz_pay"]["app_id"]


class TestChecks:
    def test_passes_a_valid_configuration(self) -> None:
        assert check_settings() == []
        with override_settings(MYANMAR_PAYMENTS={}):
            assert check_settings() == []

    def test_reports_a_malformed_setting(self) -> None:
        with override_settings(MYANMAR_PAYMENTS={"kbz": {}}):
            [error] = check_settings()
        assert error.id == "myanmar_payments.E001"
        assert error.msg.startswith("MYANMAR_PAYMENTS has unknown keys: kbz.")

    def test_reports_each_gateway_missing_a_setting(self) -> None:
        kbz = {**CONFIG["kbz_pay"], "app_key": "  "}
        wave = {key: value for key, value in CONFIG["wave_pay"].items() if key != "merchant_id"}
        with override_settings(MYANMAR_PAYMENTS={**CONFIG, "kbz_pay": kbz, "wave_pay": wave}):
            errors = check_settings()
        assert [(error.id, error.msg, error.hint) for error in errors] == [
            (
                "myanmar_payments.E002",
                "The kbz_pay configuration is missing [app_key].",
                'Set it in MYANMAR_PAYMENTS["kbz_pay"].',
            ),
            (
                "myanmar_payments.E002",
                "The wave_pay configuration is missing [merchant_id].",
                'Set it in MYANMAR_PAYMENTS["wave_pay"].',
            ),
        ]

    def test_reports_factories_that_cannot_be_imported(self) -> None:
        value = {**CONFIG, "http_client": "tests.nowhere", "async_http_client": "tests.helpers"}
        with override_settings(MYANMAR_PAYMENTS=value):
            errors = check_settings()
        assert [error.id for error in errors] == ["myanmar_payments.E003"] * 2

    def test_only_runs_for_this_app(self) -> None:
        with override_settings(MYANMAR_PAYMENTS={"kbz": {}}):
            assert check_settings(app_configs=[]) == []
            ours = apps.get_app_config("django_myanmar_payments")
            assert len(check_settings(app_configs=[ours])) == 1

    def test_is_registered_with_manage_py_check(self) -> None:
        call_command("check", verbosity=0)
        with (
            override_settings(MYANMAR_PAYMENTS={"kbz_pay": {}}),
            pytest.raises(SystemCheckError, match=r"myanmar_payments\.E002"),
        ):
            call_command("check", verbosity=0)
