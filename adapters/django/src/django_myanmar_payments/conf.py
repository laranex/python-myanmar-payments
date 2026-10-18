"""Reads and checks the ``MYANMAR_PAYMENTS`` setting."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string
from python_myanmar_payments import (
    AyaPayConfig,
    CyberSourceConfig,
    KbzPayConfig,
    WavePayConfig,
    YomaMmqrConfig,
)

__all__ = ["GATEWAYS", "OPTIONS", "PaymentsSettings", "import_factory", "payments_settings"]

GATEWAYS: Mapping[str, type] = {
    "kbz_pay": KbzPayConfig,
    "wave_pay": WavePayConfig,
    "aya_pay": AyaPayConfig,
    "yoma_mmqr": YomaMmqrConfig,
    "cyber_source": CyberSourceConfig,
}
"""The gateway keys of ``MYANMAR_PAYMENTS`` and the core config class each one takes."""

OPTIONS = ("token_cache", "http_client", "async_http_client")
"""The other keys of ``MYANMAR_PAYMENTS``, each a non-empty string."""


@dataclass(frozen=True)
class PaymentsSettings:
    """``MYANMAR_PAYMENTS``, checked. Error messages name keys, never values."""

    gateways: Mapping[str, Any]
    """Each configured gateway's mapping or core config object. Empty: read the environment."""
    token_cache: str | None = None
    """A ``CACHES`` alias that stores Yoma MMQR access tokens."""
    http_client: str | None = None
    """The dotted path of a function returning a new ``httpx.Client``."""
    async_http_client: str | None = None
    """The dotted path of a function returning a new ``httpx.AsyncClient``."""

    @property
    def from_env(self) -> bool:
        """Whether gateways are read from ``KBZ_PAY_*``, ``WAVE_PAY_*``, ... variables."""
        return not self.gateways


def payments_settings() -> PaymentsSettings:
    """``settings.MYANMAR_PAYMENTS`` checked, or ``ImproperlyConfigured``.

    A missing setting, or one without gateway keys, reads every gateway from the
    environment when it is first used.
    """
    value = getattr(settings, "MYANMAR_PAYMENTS", {})
    if not isinstance(value, Mapping):
        raise ImproperlyConfigured("MYANMAR_PAYMENTS must be a dict.")

    unknown = sorted(str(key) for key in value if key not in GATEWAYS and key not in OPTIONS)
    if unknown:
        allowed = ", ".join([*GATEWAYS, *OPTIONS])
        raise ImproperlyConfigured(
            f"MYANMAR_PAYMENTS has unknown keys: {', '.join(unknown)}. Allowed keys: {allowed}."
        )

    gateways: dict[str, Any] = {}
    for name, config_class in GATEWAYS.items():
        if name not in value:
            continue
        if not isinstance(value[name], (Mapping, config_class)):
            raise ImproperlyConfigured(
                f'MYANMAR_PAYMENTS["{name}"] must be a dict or a {config_class.__name__}.'
            )
        gateways[name] = value[name]

    options: dict[str, str] = {}
    for option in OPTIONS:
        if option not in value:
            continue
        if not isinstance(value[option], str) or value[option] == "":
            raise ImproperlyConfigured(f'MYANMAR_PAYMENTS["{option}"] must be a non-empty string.')
        options[option] = value[option]

    alias = options.get("token_cache")
    if alias is not None and alias not in settings.CACHES:
        raise ImproperlyConfigured(
            f'MYANMAR_PAYMENTS["token_cache"] is "{alias}", which is not a CACHES alias.'
        )
    return PaymentsSettings(gateways=gateways, **options)


def import_factory(option: str, path: str) -> Callable[[], Any]:
    """The function at ``path``, or ``ImproperlyConfigured`` naming ``option``."""
    try:
        factory = import_string(path)
    except ImportError as error:
        raise ImproperlyConfigured(
            f'MYANMAR_PAYMENTS["{option}"] is "{path}", which cannot be imported.'
        ) from error
    if not callable(factory):
        raise ImproperlyConfigured(f'MYANMAR_PAYMENTS["{option}"] is "{path}", not a function.')
    return factory  # type: ignore[no-any-return]
