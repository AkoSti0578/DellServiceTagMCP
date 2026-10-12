"""Client for the Dell TechDirect Warranty API (v5).

Credentials (client id / secret) are requested via https://tdm.dell.com
(TechDirect -> APIs -> Warranty API). The API uses OAuth2 client credentials.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://apigtwb2c.us.dell.com"
DEFAULT_TOKEN_PATH = "/auth/oauth/v2/token"
DEFAULT_API_PATH = "/PROD/sbil/eapi/v5"

# The asset-entitlements endpoint accepts at most 100 service tags per call.
MAX_TAGS_PER_REQUEST = 100


class DellApiError(RuntimeError):
    """Raised when the Dell API cannot be reached or returns an error."""


class MissingCredentialsError(DellApiError):
    pass


@dataclass(frozen=True)
class DellApiSettings:
    client_id: str | None
    client_secret: str | None
    base_url: str = DEFAULT_BASE_URL
    token_path: str = DEFAULT_TOKEN_PATH
    api_path: str = DEFAULT_API_PATH
    timeout: float = 30.0

    @classmethod
    def from_env(cls) -> DellApiSettings:
        return cls(
            client_id=os.getenv("DELL_CLIENT_ID") or None,
            client_secret=os.getenv("DELL_CLIENT_SECRET") or None,
            base_url=os.getenv("DELL_API_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            token_path=os.getenv("DELL_TOKEN_PATH", DEFAULT_TOKEN_PATH),
            api_path=os.getenv("DELL_API_PATH", DEFAULT_API_PATH).rstrip("/"),
            timeout=float(os.getenv("DELL_API_TIMEOUT", "30")),
        )

    @property
    def has_credentials(self) -> bool:
        return bool(self.client_id and self.client_secret)


class DellWarrantyClient:
    def __init__(
        self,
        settings: DellApiSettings | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or DellApiSettings.from_env()
        self._http = http_client or httpx.AsyncClient(timeout=self.settings.timeout)
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get_token(self) -> str:
        if not self.settings.has_credentials:
            raise MissingCredentialsError(
                "Dell API-credentials ontbreken. Zet DELL_CLIENT_ID en DELL_CLIENT_SECRET "
                "(aan te vragen via Dell TechDirect: https://tdm.dell.com)."
            )
        async with self._token_lock:
            if self._token and time.monotonic() < self._token_expires_at:
                return self._token
            try:
                resp = await self._http.post(
                    self.settings.base_url + self.settings.token_path,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self.settings.client_id,
                        "client_secret": self.settings.client_secret,
                    },
                )
            except httpx.HTTPError as exc:
                raise DellApiError(f"Kan Dell OAuth-server niet bereiken: {exc}") from exc
            if resp.status_code != 200:
                raise DellApiError(
                    f"Dell OAuth-token aanvragen mislukt (HTTP {resp.status_code}): {resp.text[:300]}"
                )
            payload = resp.json()
            self._token = payload["access_token"]
            # Refresh a minute early to avoid using a token right as it expires.
            self._token_expires_at = time.monotonic() + max(int(payload.get("expires_in", 3600)) - 60, 30)
            return self._token

    async def _get(self, endpoint: str, params: dict[str, str]) -> Any:
        url = f"{self.settings.base_url}{self.settings.api_path}/{endpoint}"
        for attempt in range(2):
            token = await self._get_token()
            try:
                resp = await self._http.get(
                    url,
                    params=params,
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                )
            except httpx.HTTPError as exc:
                raise DellApiError(f"Kan Dell API niet bereiken: {exc}") from exc
            if resp.status_code == 401 and attempt == 0:
                # Token revoked or expired early: fetch a new one and retry once.
                self._token = None
                continue
            if resp.status_code != 200:
                raise DellApiError(
                    f"Dell API {endpoint} gaf HTTP {resp.status_code}: {resp.text[:300]}"
                )
            return resp.json()
        raise DellApiError("Dell API weigert het OAuth-token (HTTP 401).")

    async def get_asset_entitlements(self, service_tags: list[str]) -> list[dict]:
        """Warranty + product info for up to any number of tags (batched per 100)."""
        results: list[dict] = []
        for i in range(0, len(service_tags), MAX_TAGS_PER_REQUEST):
            batch = service_tags[i : i + MAX_TAGS_PER_REQUEST]
            data = await self._get("asset-entitlements", {"servicetags": ",".join(batch)})
            results.extend(data if isinstance(data, list) else [data])
        return results

    async def get_asset_components(self, service_tag: str) -> dict:
        """Original factory configuration (parts list) of one system."""
        data = await self._get("asset-components", {"servicetag": service_tag})
        return data[0] if isinstance(data, list) and data else data


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _date_only(value: str | None) -> str | None:
    dt = _parse_dt(value)
    return dt.date().isoformat() if dt else value


def summarize_asset(asset: dict, now: datetime | None = None) -> dict:
    """Turn a raw asset-entitlements record into a compact, readable summary."""
    now = now or datetime.now(UTC)
    tag = asset.get("serviceTag")

    if asset.get("invalid"):
        return {"serviceTag": tag, "found": False, "message": "Service tag onbekend bij Dell."}

    entitlements = []
    latest_end: datetime | None = None
    for ent in asset.get("entitlements") or []:
        start = _parse_dt(ent.get("startDate"))
        end = _parse_dt(ent.get("endDate"))
        if end and (latest_end is None or end > latest_end):
            latest_end = end
        entitlements.append(
            {
                "service": ent.get("serviceLevelDescription"),
                "serviceLevelCode": ent.get("serviceLevelCode"),
                "type": ent.get("entitlementType"),
                "startDate": start.date().isoformat() if start else None,
                "endDate": end.date().isoformat() if end else None,
                "active": bool(end and end >= now and (start is None or start <= now)),
            }
        )
    entitlements.sort(key=lambda e: e["endDate"] or "", reverse=True)

    in_warranty = any(e["active"] for e in entitlements)
    days_left = (latest_end - now).days if latest_end and latest_end >= now else 0

    return {
        "serviceTag": tag,
        "found": True,
        "model": asset.get("productLineDescription") or asset.get("systemDescription"),
        "productFamily": asset.get("productFamily"),
        "productLine": asset.get("productLobDescription"),
        "productId": asset.get("productId"),
        "shipDate": _date_only(asset.get("shipDate")),
        "countryCode": asset.get("countryCode"),
        "warrantyStatus": "Actief" if in_warranty else ("Verlopen" if entitlements else "Geen garantie gevonden"),
        "warrantyEndDate": latest_end.date().isoformat() if latest_end else None,
        "daysOfWarrantyLeft": days_left,
        "duplicated": bool(asset.get("duplicated")),
        "entitlements": entitlements,
    }


def summarize_components(data: dict) -> dict:
    components = [
        {
            "itemNumber": c.get("itemNumber"),
            "description": c.get("itemDescription"),
            "partNumber": c.get("partNumber"),
            "partDescription": c.get("partDescription"),
            "quantity": c.get("partQuantity") or c.get("itemQuantity"),
        }
        for c in data.get("components") or []
    ]
    return {
        "serviceTag": data.get("serviceTag"),
        "model": data.get("productLineDescription") or data.get("systemDescription"),
        "shipDate": _date_only(data.get("shipDate")),
        "componentCount": len(components),
        "components": components,
    }
