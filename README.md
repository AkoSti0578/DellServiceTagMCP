# Dell Service Tag MCP

Een [MCP](https://modelcontextprotocol.io)-server die informatie over **Dell service tags** opvraagt via de
officiële Dell TechDirect Warranty API (v5). Geef Claude een of meer service tags en je krijgt:

- model, productfamilie en productlijn
- verzenddatum en land
- garantiestatus (**Actief** / **Verlopen**), einddatum en resterende dagen
- alle supportcontracten (bijv. ProSupport, Next Business Day Onsite) met start- en einddatum
- de originele fabrieksconfiguratie (onderdelen en onderdeelnummers)
- Express Service Code en een link naar de Dell supportpagina

De server draait **lokaal in Claude** (Claude Desktop / Claude Code, via stdio) of als
**HTTP-server op Azure App Service** (Streamable HTTP, met API-key), zodat je hem ook als
remote connector kunt gebruiken.

## Tools

| Tool | Wat het doet | Dell API nodig |
|---|---|---|
| `lookup_service_tags` | Model, verzenddatum, garantiestatus en contracten voor één of meer tags (tot 500 per aanroep, intern per 100 gebundeld) | ja |
| `get_service_tag_components` | Originele fabrieksconfiguratie (onderdelenlijst) van één tag | ja |
| `validate_service_tags` | Controleert het formaat, geeft Express Service Code + supportlink | nee |

Voorbeeldvragen aan Claude:

> Wat is de garantiestatus van service tags 1ABC234, 5DEF678 en 9GHJ012?
>
> Welke onderdelen zaten er origineel in Dell 1ABC234?
>
> Welke van deze laptops valt binnen 3 maanden uit de garantie? (plak een lijst met tags)

## 1. Dell API-credentials aanvragen

1. Log in op [Dell TechDirect](https://tdm.dell.com) (gratis account voor zakelijke klanten/partners).
2. Ga naar **Services → APIs** en vraag toegang aan tot de **Warranty API** (*Basic Warranty and Entitlement*).
3. Na goedkeuring (meestal enkele werkdagen) krijg je een **Client ID** en **Client Secret**.

Zonder credentials werkt alleen `validate_service_tags`; de andere tools geven een duidelijke
foutmelding plus de offline informatie (supportlink en Express Service Code).

## 2. Installeren

Vereist Python 3.11+.

```bash
git clone https://github.com/AkoSti0578/DellServiceTagMCP.git
cd DellServiceTagMCP
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install .
```

Of zonder installatie met [uv](https://docs.astral.sh/uv/):
`uvx --from git+https://github.com/AkoSti0578/DellServiceTagMCP dell-servicetag-mcp`

## 3. Gebruiken in Claude

### Claude Code

```bash
claude mcp add dell-servicetag \
  -e DELL_CLIENT_ID=<client-id> -e DELL_CLIENT_SECRET=<client-secret> \
  -- dell-servicetag-mcp
```

Of kopieer `.mcp.json.example` naar `.mcp.json` in je project en vul de credentials in.

### Claude Desktop

Open *Settings → Developer → Edit Config* (`claude_desktop_config.json`) en voeg toe
(zie ook `claude_desktop_config.example.json`):

```json
{
  "mcpServers": {
    "dell-servicetag": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/AkoSti0578/DellServiceTagMCP", "dell-servicetag-mcp"],
      "env": {
        "DELL_CLIENT_ID": "<client-id>",
        "DELL_CLIENT_SECRET": "<client-secret>"
      }
    }
  }
}
```

Heb je het pakket in een venv geïnstalleerd, gebruik dan als `command` het volledige pad naar
`dell-servicetag-mcp` (bijv. `C:\\pad\\naar\\.venv\\Scripts\\dell-servicetag-mcp.exe`) en laat `args` weg.
Herstart Claude Desktop daarna.

### Remote (via Azure, zie hieronder)

- **Claude Code:**
  `claude mcp add --transport http dell-servicetag https://<app>.azurewebsites.net/mcp --header "Authorization: Bearer <MCP_API_KEY>"`
- **Claude.ai / Claude Desktop custom connector:** voeg `https://<app>.azurewebsites.net/mcp` toe als
  connector. Custom connectors ondersteunen geen vaste headers; zet de server dan alleen open achter
  een netwerkbeperking, of gebruik lokaal de stdio-variant.

## 4. Deployen op Azure App Service (optioneel)

De HTTP-modus is stateless (geen sticky sessions nodig) en heeft een `/health` endpoint voor
health probes.

### Optie A: script (code-deploy, Linux Python)

```bash
az login
export DELL_CLIENT_ID=<client-id> DELL_CLIENT_SECRET=<client-secret>
export APP_NAME=mijn-dell-mcp            # optioneel, moet wereldwijd uniek zijn
./deploy/azure-deploy.sh
```

Het script maakt een resource group en Linux App Service (B1, Python 3.12) aan, zet de
app settings, stelt `bash startup.sh` in als startup command, zet HTTPS-only/Always On aan
en toont aan het eind het MCP endpoint en de gegenereerde API-key.

### Optie B: container

```bash
az acr build --registry <acr-naam> --image dell-servicetag-mcp:latest .
az webapp create -g <rg> -p <plan> -n <app> \
  --deployment-container-image-name <acr-naam>.azurecr.io/dell-servicetag-mcp:latest
az webapp config appsettings set -g <rg> -n <app> --settings \
  WEBSITES_PORT=8000 DELL_CLIENT_ID=... DELL_CLIENT_SECRET=... MCP_API_KEY=... \
  MCP_ALLOWED_HOSTS=<app>.azurewebsites.net
```

Lokaal testen van de container:

```bash
docker build -t dell-servicetag-mcp .
docker run -p 8000:8000 --env-file .env dell-servicetag-mcp
curl http://localhost:8000/health
```

**Tip:** bewaar `DELL_CLIENT_SECRET` en `MCP_API_KEY` in Azure Key Vault en verwijs ernaar met
een Key Vault reference (`@Microsoft.KeyVault(SecretUri=...)`) in de app settings.

## Configuratie (omgevingsvariabelen)

| Variabele | Omschrijving |
|---|---|
| `DELL_CLIENT_ID` / `DELL_CLIENT_SECRET` | Dell TechDirect API-credentials (verplicht voor online lookups) |
| `MCP_API_KEY` | HTTP-modus: API-key(s), komma-gescheiden. Mee te sturen als `Authorization: Bearer <key>` of `X-API-Key: <key>`. Zonder deze variabele is `/mcp` onbeveiligd. |
| `MCP_ALLOWED_HOSTS` | HTTP-modus: toegestane `Host` headers (DNS-rebinding bescherming), bijv. `mijnapp.azurewebsites.net` |
| `MCP_ALLOWED_ORIGINS` | HTTP-modus: toegestane `Origin` headers (alleen relevant met `MCP_ALLOWED_HOSTS`) |
| `MCP_TRANSPORT` | `stdio` (standaard) of `http` |
| `PORT` | Poort voor HTTP-modus (standaard 8000) |
| `DELL_API_BASE_URL` | Standaard `https://apigtwb2c.us.dell.com` |
| `DELL_API_TIMEOUT` | Timeout in seconden (standaard 30) |
| `LOG_LEVEL` | Standaard `INFO` |

## Ontwikkelen

```bash
pip install -e ".[dev]"
pytest
dell-servicetag-mcp --transport http      # lokaal HTTP op poort 8000
npx @modelcontextprotocol/inspector       # interactief testen
```

De tests mocken de Dell API, dus er zijn geen credentials nodig.

## Opmerkingen

- De Dell API accepteert maximaal 100 tags per request; de server bundelt grotere lijsten automatisch.
- Het OAuth-token wordt gecachet en automatisch vernieuwd (ook na een onverwachte HTTP 401).
- Service tags worden genormaliseerd: hoofdletters, spaties/komma's/puntkomma's als scheidingsteken, dubbele tags verwijderd.
