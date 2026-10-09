"""The incoming gateway request that callbacks are verified against."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from types import MappingProxyType
from typing import Any
from urllib.parse import parse_qsl

from ._json import LosslessObject, dumps, parse_object, to_plain_object

__all__ = ["BodyInput", "CallbackRequest", "HeadersInput", "QueryInput"]

HeaderValue = str | Sequence[str] | None

HeadersInput = Mapping[str, HeaderValue] | Iterable[tuple[str, str]]
"""Headers as a mapping (Django, Flask, Starlette and plain dicts) or ``(name, value)`` pairs."""

QueryInput = str | bytes | Mapping[str, HeaderValue] | Iterable[tuple[str, str]]
"""A query string (``str`` or ``bytes``), a mapping of values or ``(name, value)`` pairs."""

BodyInput = bytes | bytearray | memoryview | str
"""A request body: raw bytes (preferred) or text."""

# JavaScript's String.prototype.trim() whitespace, which includes the byte order mark.
_WHITESPACE = "".join(
    chr(code)
    for code in (
        *(0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20, 0xA0, 0x1680),
        *range(0x2000, 0x200B),
        *(0x2028, 0x2029, 0x202F, 0x205F, 0x3000, 0xFEFF),
    )
)


class CallbackRequest:
    """An incoming request from a gateway (a server callback or a browser return).

    Signatures are verified against what the gateway actually sent, so build it
    from the real request: the raw body bytes, the headers and the query string.

    .. code-block:: python

        CallbackRequest(body=request.body, headers=request.headers,
                        query=request.META["QUERY_STRING"])
    """

    __slots__ = ("_headers", "_query", "raw_body")

    raw_body: bytes
    """The raw request body exactly as received."""

    def __init__(
        self,
        body: BodyInput | None = None,
        headers: HeadersInput | None = None,
        query: QueryInput | None = None,
    ) -> None:
        if body is None:
            self.raw_body = b""
        elif isinstance(body, str):
            self.raw_body = body.encode("utf-8", errors="surrogatepass")
        else:
            self.raw_body = bytes(body)
        self._headers: Mapping[str, str] = MappingProxyType(_normalize_headers(headers))
        self._query: Mapping[str, str] = MappingProxyType(_normalize_query(query))

    @classmethod
    def from_json(
        cls, payload: Mapping[str, Any], headers: HeadersInput | None = None
    ) -> CallbackRequest:
        """Builds a request from a decoded payload, encoded as a JSON body.

        Use it to replay a callback stored as JSON. Decimals are written as
        strings.
        """
        merged = {**_normalize_headers(headers), "content-type": "application/json"}
        return cls(body=dumps(payload), headers=merged)

    @property
    def body(self) -> str:
        """The raw request body, decoded as UTF-8."""
        return self.raw_body.decode("utf-8", errors="replace")

    @property
    def headers(self) -> Mapping[str, str]:
        """The request headers, with lowercase names. Repeated headers are joined with ``, ``."""
        return self._headers

    @property
    def query(self) -> Mapping[str, str]:
        """The query string parameters (the first value of each)."""
        return self._query

    def header(self, name: str) -> str | None:
        """The named header, matched case-insensitively, or ``None``."""
        return self._headers.get(name.lower())

    def parsed_body(self) -> dict[str, Any]:
        """The body decoded as JSON or as a urlencoded form.

        JSON numbers keep their exact text as strings, e.g. ``"1000.50"``.
        """
        return to_plain_object(lossless_body(self))

    def input(self) -> dict[str, Any]:
        """The parsed body merged over the query string."""
        return to_plain_object(lossless_input(self))

    def query_input(self) -> dict[str, Any]:
        """The query string merged over the parsed body."""
        return to_plain_object(lossless_query_input(self))

    def __repr__(self) -> str:
        return (
            f"CallbackRequest(body={self.body!r}, headers={dict(self._headers)!r}, "
            f"query={dict(self._query)!r})"
        )


def lossless_body(request: CallbackRequest) -> LosslessObject:
    """The body as JSON (numbers kept exact) or a form, or ``{}``."""
    body = request.body.strip(_WHITESPACE)
    if body == "":
        return {}
    json = parse_object(body)
    if json is not None:
        return json
    return dict(_first_values(parse_qsl(body, keep_blank_values=True)))


def lossless_input(request: CallbackRequest) -> LosslessObject:
    """The parsed body merged over the query string."""
    return {**request.query, **lossless_body(request)}


def lossless_query_input(request: CallbackRequest) -> LosslessObject:
    """The query string merged over the parsed body."""
    return {**lossless_body(request), **request.query}


def _first_values(pairs: Iterable[tuple[str, str]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in pairs:
        result.setdefault(key, value)
    return result


def _pairs(source: Any) -> Iterable[tuple[str, Any]]:
    items = getattr(source, "items", None)
    if callable(items):
        return list(items())
    return list(source)


def _normalize_headers(headers: HeadersInput | None) -> dict[str, str]:
    result: dict[str, str] = {}
    if headers is None:
        return result
    for name, value in _pairs(headers):
        if value is None:
            continue
        text = value if isinstance(value, str) else ", ".join(value)
        key = str(name).lower()
        result[key] = text if key not in result else f"{result[key]}, {text}"
    return result


def _normalize_query(query: QueryInput | None) -> dict[str, str]:
    if query is None:
        return {}
    if isinstance(query, bytes):
        query = query.decode("utf-8", errors="replace")
    if isinstance(query, str):
        text = query[1:] if query.startswith("?") else query
        return _first_values(parse_qsl(text, keep_blank_values=True))
    lists = getattr(query, "lists", None)
    if callable(lists):  # Django's QueryDict and Werkzeug's MultiDict.
        return {key: values[0] for key, values in lists() if values}
    multi_items = getattr(query, "multi_items", None)
    if callable(multi_items):  # Starlette's QueryParams.
        return _first_values(multi_items())
    result: dict[str, str] = {}
    for key, value in _pairs(query):
        first = value if isinstance(value, str) or value is None else next(iter(value), None)
        if first is not None:
            result.setdefault(key, first)
    return result
