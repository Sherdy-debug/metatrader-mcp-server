"""
Network access protection for the HTTP based servers (MCP SSE / streamable HTTP and the REST API).

- Binding to a loopback address needs no extra configuration.
- Binding to any other address requires a bearer token (``MCP_AUTH_TOKEN``)
  and TLS (certificate and key), unless TLS is explicitly declared to be
  provided by a reverse proxy (``MCP_ALLOW_INSECURE_REMOTE=true``).
"""

import hmac
import ipaddress
import json
import os
from typing import Iterable, Optional

MIN_TOKEN_LENGTH = 32
LOOPBACK_NAMES = {"localhost"}


def is_loopback(host: Optional[str]) -> bool:
    """True when ``host`` only accepts connections from the local machine."""
    if not host:
        return False
    host = host.strip().strip("[]").lower()
    if host in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def check_network_security(
    host: str,
    *,
    token: Optional[str],
    ssl_certfile: Optional[str] = None,
    ssl_keyfile: Optional[str] = None,
    allow_insecure_remote: bool = False,
    token_env: str = "MCP_AUTH_TOKEN",
) -> None:
    """
    Refuse insecure network configurations.

    Raises:
        ValueError: With a description of what is missing.
    """
    if token is not None and len(token) < MIN_TOKEN_LENGTH:
        raise ValueError(f"{token_env} must have at least {MIN_TOKEN_LENGTH} characters")
    if bool(ssl_certfile) != bool(ssl_keyfile):
        raise ValueError("Both the TLS certificate and key files are required")
    if is_loopback(host):
        return
    if not token:
        raise ValueError(
            f"Refusing to listen on {host} without authentication: set {token_env} "
            f"(at least {MIN_TOKEN_LENGTH} random characters) or bind to 127.0.0.1"
        )
    if not ssl_certfile and not allow_insecure_remote:
        raise ValueError(
            f"Refusing to listen on {host} without TLS: provide a certificate and key, "
            "or set MCP_ALLOW_INSECURE_REMOTE=true if a reverse proxy terminates TLS"
        )


class BearerTokenMiddleware:
    """
    Pure ASGI middleware that requires ``Authorization: Bearer <token>``.

    It does not buffer responses, so it is safe for SSE and streaming responses.
    CORS preflight requests (OPTIONS) and the paths in ``exempt_paths`` pass through.
    """

    def __init__(self, app, token: str, exempt_paths: Iterable[str] = ()):
        if not token:
            raise ValueError("A token is required")
        self.app = app
        self._token = token.encode("utf-8")
        self._exempt = set(exempt_paths)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        if scope["type"] == "http" and scope.get("method") == "OPTIONS":
            return await self.app(scope, receive, send)
        if scope.get("path") in self._exempt:
            return await self.app(scope, receive, send)

        authorization = b""
        for name, value in scope.get("headers", []):
            if name.lower() == b"authorization":
                authorization = value
                break
        scheme, _, credentials = authorization.partition(b" ")
        if scheme.lower() == b"bearer" and hmac.compare_digest(credentials.strip(), self._token):
            return await self.app(scope, receive, send)

        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = json.dumps({"error": "unauthorized"}).encode()
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"www-authenticate", b"Bearer"),
            ],
        })
        await send({"type": "http.response.body", "body": body})
