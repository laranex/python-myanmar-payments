from __future__ import annotations

import pytest

from python_myanmar_payments import _support
from tests.helpers import NOW


class Clock:
    def __init__(self) -> None:
        self.now: float = NOW


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """Freezes the package's clock at ``NOW``."""
    current = Clock()
    monkeypatch.setattr(_support, "current_time", lambda: current.now)
    return current


@pytest.fixture(params=["sync", "async"])
def mode(request: pytest.FixtureRequest) -> str:
    """Runs a test against the sync and the async client."""
    value: str = request.param
    return value
