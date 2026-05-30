"""SSRF guard tests for web_fetch.

We can't easily exercise the live httpx path in a unit test, but we *can*
verify that the SSRF guard rejects private/loopback/metadata-service hosts
before the network call.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from co_scientist.config import Config
from co_scientist.tools import web_fetch as web_fetch_mod
from co_scientist.tools.base import ToolCtx
from co_scientist.tools.web_fetch import WebFetchTool, _is_private_ip


def test_private_ip_helper_blocks_loopback() -> None:
    assert _is_private_ip("127.0.0.1") is True
    assert _is_private_ip("localhost") is True


def test_private_ip_helper_blocks_link_local_metadata() -> None:
    # AWS / GCP metadata service
    assert _is_private_ip("169.254.169.254") is True


def test_private_ip_helper_blocks_rfc1918() -> None:
    assert _is_private_ip("10.0.0.1") is True
    assert _is_private_ip("192.168.1.1") is True
    assert _is_private_ip("172.16.0.1") is True


def test_private_ip_helper_allows_public() -> None:
    # 1.1.1.1 (Cloudflare DNS) is a stable public address
    assert _is_private_ip("1.1.1.1") is False


@pytest.mark.asyncio
async def test_web_fetch_rejects_loopback_url() -> None:
    tool = WebFetchTool(Config())
    res = await tool.call({"url": "http://127.0.0.1/admin"}, ToolCtx(cfg=Config(), db=None))
    assert res.is_error
    assert "private" in (res.error_message or "").lower()


@pytest.mark.asyncio
async def test_web_fetch_rejects_metadata_url() -> None:
    tool = WebFetchTool(Config())
    res = await tool.call(
        {"url": "http://169.254.169.254/latest/meta-data/"},
        ToolCtx(cfg=Config(), db=None),
    )
    assert res.is_error
    assert "private" in (res.error_message or "").lower()


@pytest.mark.asyncio
async def test_web_fetch_rejects_unsupported_scheme() -> None:
    tool = WebFetchTool(Config())
    res = await tool.call({"url": "file:///etc/passwd"}, ToolCtx(cfg=Config(), db=None))
    assert res.is_error


@pytest.mark.asyncio
async def test_web_fetch_rejects_advertised_oversized_download(monkeypatch) -> None:
    cfg = Config()
    cfg.web_fetch.max_bytes = 10
    monkeypatch.setattr(web_fetch_mod, "_is_private_ip", lambda host: False)
    monkeypatch.setattr(web_fetch_mod.httpx, "AsyncClient", _fake_client_factory(
        _FakeResponse(headers={"content-length": "11"}, chunks=[b"not-read"])
    ))

    res = await WebFetchTool(cfg).call(
        {"url": "https://example.test/too-large.pdf"},
        ToolCtx(cfg=cfg, db=None),
    )

    assert res.is_error
    assert "too large" in (res.error_message or "")


@pytest.mark.asyncio
async def test_web_fetch_stops_streaming_when_body_exceeds_limit(monkeypatch) -> None:
    cfg = Config()
    cfg.web_fetch.max_bytes = 10
    monkeypatch.setattr(web_fetch_mod, "_is_private_ip", lambda host: False)
    monkeypatch.setattr(web_fetch_mod.httpx, "AsyncClient", _fake_client_factory(
        _FakeResponse(headers={}, chunks=[b"12345", b"678901"])
    ))

    res = await WebFetchTool(cfg).call(
        {"url": "https://example.test/streaming"},
        ToolCtx(cfg=cfg, db=None),
    )

    assert res.is_error
    assert "too large" in (res.error_message or "")


class _FakeResponse:
    def __init__(self, *, headers: dict[str, str], chunks: list[bytes]) -> None:
        self.status_code = 200
        self.headers = headers
        self.url = "https://example.test/final"
        self.request = SimpleNamespace()
        self._chunks = chunks

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


class _FakeStream:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _FakeClient:
    def __init__(self, response: _FakeResponse, *args, **kwargs) -> None:
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def stream(self, method: str, url: str) -> _FakeStream:
        return _FakeStream(self._response)


def _fake_client_factory(response: _FakeResponse):
    def _factory(*args, **kwargs):
        return _FakeClient(response, *args, **kwargs)

    return _factory
