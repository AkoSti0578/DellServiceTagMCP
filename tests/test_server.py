from datetime import UTC, datetime

import json

import httpx
import pytest
import respx
from mcp.client import Client

from dell_servicetag_mcp import server
from dell_servicetag_mcp.dell_api import DellApiSettings, DellWarrantyClient, summarize_asset
from dell_servicetag_mcp.servicetag import express_service_code, normalize_service_tags

BASE = "https://dell.test"
TOKEN_URL = f"{BASE}/auth/oauth/v2/token"
ENT_URL = f"{BASE}/PROD/sbil/eapi/v5/asset-entitlements"
COMP_URL = f"{BASE}/PROD/sbil/eapi/v5/asset-components"

ASSET = {
    "id": 1,
    "serviceTag": "ABC1234",
    "shipDate": "2023-01-10T06:00:00Z",
    "productLineDescription": "LATITUDE 5430",
    "productLobDescription": "Latitude",
    "productFamily": "Notebooks",
    "productId": "latitude-14-5430-laptop",
    "countryCode": "NL",
    "duplicated": False,
    "invalid": False,
    "entitlements": [
        {
            "itemNumber": "123-1",
            "startDate": "2023-01-10T00:00:00Z",
            "endDate": "2026-01-10T23:59:59Z",
            "entitlementType": "INITIAL",
            "serviceLevelCode": "ND",
            "serviceLevelDescription": "Next Business Day Onsite",
        },
        {
            "itemNumber": "123-2",
            "startDate": "2023-01-10T00:00:00Z",
            "endDate": "2028-01-10T23:59:59Z",
            "entitlementType": "EXTENDED",
            "serviceLevelCode": "PS",
            "serviceLevelDescription": "ProSupport",
        },
    ],
}


def _json(result):
    assert not result.is_error
    return json.loads(result.content[0].text)


@pytest.fixture
def api():
    with respx.mock(assert_all_called=False) as mock:
        mock.post(TOKEN_URL).respond(200, json={"access_token": "tok", "expires_in": 3600})
        yield mock


@pytest.fixture(autouse=True)
def client():
    c = DellWarrantyClient(DellApiSettings(client_id="id", client_secret="secret", base_url=BASE))
    server.set_client(c)
    yield c
    server.set_client(None)


def test_normalize_and_express_code():
    assert normalize_service_tags(["abc1234, def5678", " ABC1234 "]) == ["ABC1234", "DEF5678"]
    assert normalize_service_tags("abc1234;xyz9876") == ["ABC1234", "XYZ9876"]
    assert express_service_code("ABC1234") == str(int("ABC1234", 36))


def test_summarize_asset_active():
    s = summarize_asset(ASSET, now=datetime(2026, 10, 12, tzinfo=UTC))
    assert s["warrantyStatus"] == "Actief"
    assert s["warrantyEndDate"] == "2028-01-10"
    assert s["model"] == "LATITUDE 5430"
    assert s["shipDate"] == "2023-01-10"
    assert s["entitlements"][0]["service"] == "ProSupport"
    assert s["entitlements"][1]["active"] is False


def test_summarize_asset_expired_and_invalid():
    s = summarize_asset(ASSET, now=datetime(2029, 1, 1, tzinfo=UTC))
    assert s["warrantyStatus"] == "Verlopen"
    assert s["daysOfWarrantyLeft"] == 0
    assert summarize_asset({"serviceTag": "ZZZ9999", "invalid": True})["found"] is False


async def test_lookup_tool(api):
    route = api.get(ENT_URL).respond(200, json=[ASSET])
    async with Client(server.mcp) as c:
        result = await c.call_tool("lookup_service_tags", {"service_tags": ["abc1234", "bad!"]})
    data = _json(result)
    assert data["count"] == 2
    assert data["results"][0]["serviceTag"] == "ABC1234"
    assert data["results"][0]["found"] is True
    assert data["results"][1]["found"] is False
    req = route.calls.last.request
    assert req.url.params["servicetags"] == "ABC1234"
    assert req.headers["Authorization"] == "Bearer tok"


async def test_lookup_batches_per_100(api):
    route = api.get(ENT_URL).respond(200, json=[])
    tags = [f"A{i:06d}" for i in range(150)]
    async with Client(server.mcp) as c:
        result = await c.call_tool("lookup_service_tags", {"service_tags": tags})
    assert route.call_count == 2
    assert _json(result)["count"] == 150


async def test_token_is_cached_and_refreshed_on_401(api):
    api.get(ENT_URL).mock(
        side_effect=[httpx.Response(401), httpx.Response(200, json=[ASSET]), httpx.Response(200, json=[ASSET])]
    )
    client = server.get_client()
    await client.get_asset_entitlements(["ABC1234"])
    await client.get_asset_entitlements(["ABC1234"])
    assert api.routes[0].call_count == 2  # initial token + one refresh after 401


async def test_missing_credentials_returns_offline_info():
    server.set_client(DellWarrantyClient(DellApiSettings(client_id=None, client_secret=None)))
    async with Client(server.mcp) as c:
        result = await c.call_tool("lookup_service_tags", {"service_tags": ["ABC1234"]})
    data = _json(result)
    assert "DELL_CLIENT_ID" in data["error"]
    assert data["offlineInfo"][0]["supportUrl"].endswith("/ABC1234/overview")


async def test_components_tool(api):
    api.get(COMP_URL).respond(
        200,
        json={
            "serviceTag": "ABC1234",
            "productLineDescription": "LATITUDE 5430",
            "shipDate": "2023-01-10T06:00:00Z",
            "components": [
                {"itemNumber": "1", "itemDescription": "CPU", "partNumber": "X1", "partDescription": "i5", "partQuantity": 1}
            ],
        },
    )
    async with Client(server.mcp) as c:
        result = await c.call_tool("get_service_tag_components", {"service_tag": "abc1234"})
    data = _json(result)
    assert data["componentCount"] == 1
    assert data["components"][0]["partNumber"] == "X1"


async def test_validate_tool_offline():
    async with Client(server.mcp) as c:
        tools = {t.name for t in (await c.list_tools()).tools}
        result = await c.call_tool("validate_service_tags", {"service_tags": "ABC1234, toolongtag"})
    assert tools == {"lookup_service_tags", "get_service_tag_components", "validate_service_tags"}
    res = _json(result)["results"]
    assert res[0]["validFormat"] is True and res[1]["validFormat"] is False
