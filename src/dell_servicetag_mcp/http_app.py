"""ASGI app for running the MCP server over Streamable HTTP (e.g. Azure App Service).

Endpoints:
  POST /mcp      MCP Streamable HTTP endpoint
  GET  /health   health check (no auth), for Azure App Service health probes

Authentication: set MCP_API_KEY and send it as `Authorization: Bearer <key>`
or `X-API-Key: <key>`. Without MCP_API_KEY the endpoint is open (only for testing!).

Start with: dell-servicetag-mcp --transport http
        or: uvicorn --factory dell_servicetag_mcp.http_app:create_app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import hmac
import json
import logging
import os

from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .server import mcp

logger = logging.getLogger(__name__)

PUBLIC_PATHS = {"/health", "/"}


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/", methods=["GET"])
async def index(_: Request) -> JSONResponse:
    return JSONResponse({"name": "dell-servicetag-mcp", "mcpEndpoint": "/mcp", "health": "/health"})


class ApiKeyMiddleware:
    """Pure ASGI middleware (keeps streaming responses and lifespan intact)."""

    def __init__(self, app: ASGIApp, api_keys: list[str]) -> None:
        self.app = app
        self.api_keys = [k.encode() for k in api_keys]

    def _authorized(self, scope: Scope) -> bool:
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        candidate = headers.get("x-api-key", "")
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            candidate = auth[7:].strip()
        return any(hmac.compare_digest(candidate.encode(), key) for key in self.api_keys)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] not in PUBLIC_PATHS and not self._authorized(scope):
            body = json.dumps({"error": "Unauthorized"}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"www-authenticate", b"Bearer"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def create_app() -> ASGIApp:
    allowed_hosts = [h.strip() for h in os.getenv("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    security = TransportSecuritySettings(
        # DNS-rebinding protection is meant for servers on localhost. Behind Azure App
        # Service the API key protects the endpoint; optionally pin Host headers.
        enable_dns_rebinding_protection=bool(allowed_hosts),
        allowed_hosts=allowed_hosts,
        allowed_origins=[o.strip() for o in os.getenv("MCP_ALLOWED_ORIGINS", "").split(",") if o.strip()],
    )
    app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        # Stateless + JSON responses: scales horizontally across App Service instances
        # without sticky sessions.
        stateless_http=True,
        json_response=True,
        transport_security=security,
        host="0.0.0.0",
    )

    api_keys = [k.strip() for k in os.getenv("MCP_API_KEY", "").split(",") if k.strip()]
    if not api_keys:
        logger.warning("MCP_API_KEY is niet gezet: de /mcp endpoint is ONBEVEILIGD.")
        return app
    return ApiKeyMiddleware(app, api_keys)

