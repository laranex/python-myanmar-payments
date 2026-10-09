from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx

FIXTURES = Path(__file__).parent / "fixtures"
NOW = 1536637503


def fixture(path: str) -> dict[str, Any]:
    """Reads a shared test vector from tests/fixtures (the same files as the other SDKs)."""
    data: dict[str, Any] = json.loads((FIXTURES / path).read_text("utf-8"))
    return data


def hmac_hex(message: str, key: str) -> str:
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


def hmac_base64(message: str, key: str) -> str:
    digest = hmac.new(key.encode(), message.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def run(value: Any) -> Any:
    """Returns ``value``, or runs it to completion when it is a coroutine."""
    if inspect.iscoroutine(value):
        return asyncio.run(value)
    return value


@dataclass
class Recorded:
    url: str
    path: str
    method: str
    headers: dict[str, str]
    body: str

    def json(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(self.body)
        return data

    def form(self) -> dict[str, str]:
        return {key: values[0] for key, values in parse_qs(self.body, True).items()}


Reply = tuple[int, Any] | Exception


class FakeServer:
    """Answers with canned replies in order and records every request."""

    def __init__(self, *replies: Reply | Any) -> None:
        self.replies: list[Reply] = [
            reply if isinstance(reply, (tuple, Exception)) else (200, reply) for reply in replies
        ]
        self.requests: list[Recorded] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(
            Recorded(
                url=str(request.url),
                path=request.url.path,
                method=request.method,
                headers={key.lower(): value for key, value in request.headers.items()},
                body=request.content.decode(),
            )
        )
        reply: Reply = self.replies.pop(0) if self.replies else (500, {"message": "no reply"})
        if isinstance(reply, Exception):
            raise reply
        status, body = reply
        text = body if isinstance(body, str) else json.dumps(body)
        return httpx.Response(status, text=text, headers={"Content-Type": "application/json"})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def async_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def last(self) -> Recorded:
        assert self.requests, "no request was sent"
        return self.requests[-1]


def make(
    mode: str,
    sync: type[Any],
    asynchronous: type[Any],
    config: Any,
    server: FakeServer | None = None,
    **options: Any,
) -> Any:
    """The sync or async gateway for ``mode``, answering from ``server``."""
    server = server if server is not None else FakeServer()
    if mode == "sync":
        return sync(config, http_client=server.client(), **options)
    return asynchronous(config, http_client=server.async_client(), **options)
