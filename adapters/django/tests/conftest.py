from __future__ import annotations

from collections.abc import Iterator

import django
import pytest
from django.conf import settings
from django.test import override_settings

from tests import helpers
from tests.helpers import CONFIG

settings.configure(
    SECRET_KEY="test-only",
    DEBUG=False,
    ALLOWED_HOSTS=["testserver"],
    ROOT_URLCONF="tests.urls",
    INSTALLED_APPS=["django_myanmar_payments"],
    MIDDLEWARE=["django.middleware.csrf.CsrfViewMiddleware"],
    CACHES={
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
        "tokens": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "t"},
    },
    USE_TZ=True,
)
django.setup()


@pytest.fixture(autouse=True)
def payments_settings() -> Iterator[None]:
    """Every test starts from fresh facades built from CONFIG (setting_changed resets them)."""
    helpers.gateway.paths.clear()
    with override_settings(MYANMAR_PAYMENTS=CONFIG):
        yield


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
