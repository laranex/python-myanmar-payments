"""The optional app: adds ``MYANMAR_PAYMENTS`` to ``manage.py check``."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django.apps import AppConfig, apps
from django.core import checks
from django.core.exceptions import ImproperlyConfigured
from python_myanmar_payments import ConfigurationError, MyanmarPayments

from .conf import import_factory, payments_settings


class MyanmarPaymentsConfig(AppConfig):
    name = "django_myanmar_payments"
    verbose_name = "Myanmar Payments"

    def ready(self) -> None:
        checks.register(check_settings)


def check_settings(
    app_configs: Sequence[AppConfig] | None = None, **kwargs: Any
) -> list[checks.CheckMessage]:
    """Checks ``MYANMAR_PAYMENTS`` without calling any gateway.

    Explicitly configured gateways are built so a missing or invalid setting is
    reported now rather than on the first payment. Gateways read from the
    environment are only checked when used.
    """
    ours = apps.get_app_config("django_myanmar_payments")
    if app_configs is not None and ours not in app_configs:
        return []
    try:
        conf = payments_settings()
    except ImproperlyConfigured as error:
        return [checks.Error(str(error), id="myanmar_payments.E001")]

    errors: list[checks.CheckMessage] = []
    for name, config in conf.gateways.items():
        try:
            with MyanmarPayments(**{name: config}) as payments:
                getattr(payments, name)()
        except ConfigurationError as error:
            errors.append(
                checks.Error(
                    str(error),
                    hint=f'Set it in MYANMAR_PAYMENTS["{name}"].',
                    id="myanmar_payments.E002",
                )
            )
    for option in ("http_client", "async_http_client"):
        path = getattr(conf, option)
        if path is None:
            continue
        try:
            import_factory(option, path)
        except ImproperlyConfigured as error:
            errors.append(checks.Error(str(error), id="myanmar_payments.E003"))
    return errors
