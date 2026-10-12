from starlette.testclient import TestClient

from dell_servicetag_mcp.http_app import create_app

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def test_api_key_required(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", "secret-key")
    with TestClient(create_app()) as http:
        assert http.get("/health").json() == {"status": "ok"}
        assert http.post("/mcp", json=INIT, headers=HEADERS).status_code == 401
        assert http.post("/mcp", json=INIT, headers={**HEADERS, "Authorization": "Bearer wrong"}).status_code == 401

        ok = http.post("/mcp", json=INIT, headers={**HEADERS, "Authorization": "Bearer secret-key"})
        assert ok.status_code == 200
        assert ok.json()["result"]["serverInfo"]["name"] == "dell-servicetag"

        ok2 = http.post("/mcp", json=INIT, headers={**HEADERS, "X-API-Key": "secret-key"})
        assert ok2.status_code == 200


def test_allowed_hosts(monkeypatch):
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "mijnapp.azurewebsites.net")
    with TestClient(create_app()) as http:
        assert http.post("/mcp", json=INIT, headers=HEADERS).status_code in (400, 403, 421)
        ok = http.post("/mcp", json=INIT, headers={**HEADERS, "Host": "mijnapp.azurewebsites.net"})
        assert ok.status_code == 200
