"""MCP server exposing Dell service tag lookups as tools.

Run locally for Claude (stdio):   dell-servicetag-mcp
Run as HTTP server (Azure etc.):  dell-servicetag-mcp --transport http
"""

from __future__ import annotations

import argparse
import logging
import os
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from .dell_api import (
    DellApiError,
    DellWarrantyClient,
    summarize_asset,
    summarize_components,
)
from .servicetag import describe_offline, is_valid_service_tag, normalize_service_tags

logger = logging.getLogger(__name__)

MAX_TAGS_PER_CALL = 500

mcp = MCPServer(
    name="dell-servicetag",
    title="Dell Service Tag",
    version="1.0.0",
    instructions=(
        "Gebruik deze tools om informatie over Dell service tags op te vragen: model, "
        "verzenddatum, garantiestatus, einddatum van de garantie, supportcontracten en de "
        "originele fabrieksconfiguratie. Service tags zijn 7 (oudere systemen 5) tekens lang."
    ),
)

_client: DellWarrantyClient | None = None


def get_client() -> DellWarrantyClient:
    global _client
    if _client is None:
        _client = DellWarrantyClient()
    return _client


def set_client(client: DellWarrantyClient | None) -> None:
    """Override the API client (used by tests)."""
    global _client
    _client = client


ServiceTags = Annotated[
    list[str] | str,
    Field(
        description=(
            "Eén of meer Dell service tags, bijv. ['ABC1234', 'DEF5678']. "
            "Een komma- of spatiegescheiden string ('ABC1234, DEF5678') mag ook."
        )
    ),
]

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True, idempotentHint=True)


@mcp.tool(
    title="Dell service tag opzoeken",
    annotations=READ_ONLY,
)
async def lookup_service_tags(service_tags: ServiceTags) -> dict:
    """Zoek Dell service tags op: model, productfamilie, verzenddatum, land,
    garantiestatus (actief/verlopen), einddatum garantie, resterende dagen en alle
    supportcontracten (bijv. ProSupport, Next Business Day). Ondersteunt meerdere tags tegelijk."""
    tags = normalize_service_tags(service_tags)
    if not tags:
        return {"error": "Geen service tags opgegeven."}
    if len(tags) > MAX_TAGS_PER_CALL:
        return {"error": f"Maximaal {MAX_TAGS_PER_CALL} service tags per aanroep."}

    valid = [t for t in tags if is_valid_service_tag(t)]
    invalid = [t for t in tags if not is_valid_service_tag(t)]

    results: list[dict] = [
        {"serviceTag": t, "found": False, "message": "Ongeldig formaat (verwacht 5 of 7 letters/cijfers)."}
        for t in invalid
    ]
    if valid:
        try:
            assets = await get_client().get_asset_entitlements(valid)
        except DellApiError as exc:
            return {
                "error": str(exc),
                "offlineInfo": [describe_offline(t) for t in tags],
            }
        by_tag = {(a.get("serviceTag") or "").upper(): a for a in assets}
        for tag in valid:
            asset = by_tag.get(tag)
            if asset is None:
                results.append({"serviceTag": tag, "found": False, "message": "Geen gegevens ontvangen van Dell."})
            else:
                summary = summarize_asset(asset)
                summary["supportUrl"] = describe_offline(tag)["supportUrl"]
                results.append(summary)

    order = {t: i for i, t in enumerate(tags)}
    results.sort(key=lambda r: order.get(r["serviceTag"], len(order)))
    return {"count": len(results), "results": results}


@mcp.tool(
    title="Dell fabrieksconfiguratie opvragen",
    annotations=READ_ONLY,
)
async def get_service_tag_components(
    service_tag: Annotated[str, Field(description="Eén Dell service tag, bijv. 'ABC1234'.")],
) -> dict:
    """Vraag de originele fabrieksconfiguratie (onderdelenlijst: CPU, geheugen, schijven,
    onderdeelnummers) op van één Dell systeem."""
    tags = normalize_service_tags(service_tag)
    if len(tags) != 1:
        return {"error": "Geef precies één service tag op."}
    tag = tags[0]
    if not is_valid_service_tag(tag):
        return {"error": f"'{tag}' is geen geldige service tag (verwacht 5 of 7 letters/cijfers)."}
    try:
        data = await get_client().get_asset_components(tag)
    except DellApiError as exc:
        return {"error": str(exc), "offlineInfo": describe_offline(tag)}
    if not data:
        return {"serviceTag": tag, "found": False, "message": "Geen componentgegevens ontvangen van Dell."}
    summary = summarize_components(data)
    summary["serviceTag"] = summary["serviceTag"] or tag
    return summary


@mcp.tool(
    title="Service tag controleren (offline)",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, idempotentHint=True),
)
def validate_service_tags(service_tags: ServiceTags) -> dict:
    """Controleer offline (zonder Dell API) of service tags een geldig formaat hebben en
    geef de Express Service Code en de link naar de Dell supportpagina terug."""
    tags = normalize_service_tags(service_tags)
    return {"count": len(tags), "results": [describe_offline(t) for t in tags]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Dell Service Tag MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default=os.getenv("MCP_TRANSPORT", "stdio"),
        help="stdio (Claude Desktop/Code) of http (Streamable HTTP, bijv. Azure App Service)",
    )
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", os.getenv("WEBSITES_PORT", "8000"))))
    args = parser.parse_args()

    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

    if args.transport == "stdio":
        mcp.run("stdio")
    else:
        import uvicorn

        from .http_app import create_app

        uvicorn.run(create_app(), host=args.host, port=args.port, proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
