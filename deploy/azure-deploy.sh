#!/usr/bin/env bash
# Deploy de Dell Service Tag MCP server naar Azure App Service (Linux, Python, code-deploy).
#
# Vereist: Azure CLI (az login gedaan) en de volgende variabelen:
#   DELL_CLIENT_ID, DELL_CLIENT_SECRET   Dell TechDirect API-credentials
# Optioneel:
#   APP_NAME (default: dell-servicetag-mcp-<random>), RESOURCE_GROUP, LOCATION, SKU, MCP_API_KEY
set -euo pipefail

: "${DELL_CLIENT_ID:?Zet DELL_CLIENT_ID}"
: "${DELL_CLIENT_SECRET:?Zet DELL_CLIENT_SECRET}"

APP_NAME="${APP_NAME:-dell-servicetag-mcp-$RANDOM}"
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-dell-servicetag-mcp}"
LOCATION="${LOCATION:-westeurope}"
SKU="${SKU:-B1}"
MCP_API_KEY="${MCP_API_KEY:-$(openssl rand -hex 32)}"

cd "$(dirname "$0")/.."

echo ">> Resource group $RESOURCE_GROUP ($LOCATION)"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

echo ">> Web app $APP_NAME bouwen en deployen (kan enkele minuten duren)"
az webapp up \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --runtime "PYTHON:3.12" \
  --sku "$SKU" \
  --output none

echo ">> App settings en startup command"
az webapp config appsettings set \
  --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" --output none \
  --settings \
    SCM_DO_BUILD_DURING_DEPLOYMENT=true \
    DELL_CLIENT_ID="$DELL_CLIENT_ID" \
    DELL_CLIENT_SECRET="$DELL_CLIENT_SECRET" \
    MCP_API_KEY="$MCP_API_KEY" \
    MCP_ALLOWED_HOSTS="$APP_NAME.azurewebsites.net"

az webapp config set \
  --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" --output none \
  --startup-file "bash startup.sh" \
  --always-on true \
  --http20-enabled true \
  --min-tls-version 1.2

az webapp update --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" --https-only true --output none
az webapp restart --name "$APP_NAME" --resource-group "$RESOURCE_GROUP"

cat <<INFO

Klaar!
  MCP endpoint : https://$APP_NAME.azurewebsites.net/mcp
  Health check : https://$APP_NAME.azurewebsites.net/health
  API key      : $MCP_API_KEY

Bewaar de API key goed; stuur hem mee als header "Authorization: Bearer <key>".
INFO
