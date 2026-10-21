"""Finding 4: local transport by default and authentication for network access."""

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from metatrader_mcp.security import BearerTokenMiddleware, check_network_security, is_loopback
from metatrader_mcp.utils import resolve_transport_config, run_mcp

TOKEN = "x" * 40


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("MCP_TRANSPORT", "MCP_HOST", "MCP_PORT", "MCP_AUTH_TOKEN", "MCP_SSL_CERTFILE",
                 "MCP_SSL_KEYFILE", "MCP_ALLOW_INSECURE_REMOTE", "MCP_ALLOWED_HOSTS"):
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_local_stdio():
    assert resolve_transport_config() == ("stdio", "127.0.0.1", 8080)


def test_environment_overrides(monkeypatch):
    monkeypatch.setenv("MCP_TRANSPORT", "SSE")
    monkeypatch.setenv("MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("MCP_PORT", "9000")
    assert resolve_transport_config() == ("sse", "0.0.0.0", 9000)


@pytest.mark.parametrize("host, expected", [
    ("127.0.0.1", True), ("localhost", True), ("::1", True), ("[::1]", True), ("127.0.0.2", True),
    ("0.0.0.0", False), ("::", False), ("192.168.1.10", False), ("example.com", False), ("", False),
])
def test_is_loopback(host, expected):
    assert is_loopback(host) is expected


def test_loopback_needs_no_token():
    check_network_security("127.0.0.1", token=None)


def test_remote_without_token_is_refused():
    with pytest.raises(ValueError, match="without authentication"):
        check_network_security("0.0.0.0", token=None)


def test_remote_without_tls_is_refused():
    with pytest.raises(ValueError, match="without TLS"):
        check_network_security("0.0.0.0", token=TOKEN)


def test_remote_with_token_and_tls_or_proxy_is_allowed():
    check_network_security("0.0.0.0", token=TOKEN, ssl_certfile="c.pem", ssl_keyfile="k.pem")
    check_network_security("0.0.0.0", token=TOKEN, allow_insecure_remote=True)


def test_short_token_is_refused():
    with pytest.raises(ValueError, match="at least"):
        check_network_security("127.0.0.1", token="short")


def test_run_mcp_refuses_remote_without_token():
    class FakeMCP:
        def run(self, transport):
            raise AssertionError("should not start")

    with pytest.raises(ValueError):
        run_mcp(FakeMCP(), "sse", "0.0.0.0", 8080)


def app_with_token():
    app = Starlette(routes=[Route("/sse", lambda request: PlainTextResponse("ok"))])
    return BearerTokenMiddleware(app, TOKEN)


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": f"Basic {TOKEN}"}])
def test_middleware_rejects_missing_or_wrong_token(headers):
    response = TestClient(app_with_token()).get("/sse", headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_middleware_accepts_valid_token():
    response = TestClient(app_with_token()).get("/sse", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200
    assert response.text == "ok"


def test_mcp_http_apps_are_protected_with_token():
    from metatrader_mcp.server import mcp
    from metatrader_mcp.utils import build_http_app

    for transport in ("sse", "streamable-http"):
        app = build_http_app(mcp, transport, "127.0.0.1", token=TOKEN)
        assert isinstance(app, BearerTokenMiddleware)
        response = TestClient(app).get("/sse" if transport == "sse" else "/mcp")
        assert response.status_code == 401


def test_rest_api_has_no_wildcard_cors():
    from fastapi.middleware.cors import CORSMiddleware
    from metatrader_openapi.main import app

    for middleware in app.user_middleware:
        if middleware.cls is CORSMiddleware:
            assert "*" not in middleware.kwargs.get("allow_origins", [])
