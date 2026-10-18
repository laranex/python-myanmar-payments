from __future__ import annotations

import builtins
import re
from pathlib import Path

import python_myanmar_payments as core

import django_myanmar_payments as adapter

SKILL = Path(__file__).parent.parent / "skills" / "django-myanmar-payments" / "SKILL.md"

ADAPTER_NAMES = (
    r"\b(get_(?:async_)?payments|a?close_payments|(?:Async)?DjangoTokenCache"
    r"|CallbackVerificationFailed|callback_request|verify_callback|acknowledgement_response)\b"
)


def test_exports_every_public_name() -> None:
    for name in adapter.__all__:
        assert hasattr(adapter, name), name


def test_version_is_pep_440() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+((a|b|rc)\d+)?(\.dev\d+)?", adapter.__version__)


class TestSkill:
    def test_names_only_what_exists_and_covers_every_export(self) -> None:
        skill = SKILL.read_text("utf-8")
        assert skill.startswith("---\nname: django-myanmar-payments\n")
        named = set(re.findall(ADAPTER_NAMES, skill))
        assert named <= set(adapter.__all__)
        assert set(adapter.__all__) - {"__version__", "CallbackHandler"} <= named

    def test_core_classes_it_names_exist(self) -> None:
        skill = SKILL.read_text("utf-8")
        framework = {
            "CACHES",
            "ImproperlyConfigured",
            "SuspiciousOperation",
            "SynchronousOnlyOperation",
        }
        classes = set(re.findall(r"`([A-Z][A-Za-z]+)[`(]", skill)) - framework
        for name in sorted(classes - set(adapter.__all__)):
            assert hasattr(core, name) or hasattr(builtins, name), name
