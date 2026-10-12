#!/usr/bin/env bash
# Startup command for Azure App Service (Linux, Python): "bash startup.sh"
set -e
cd "$(dirname "$0")"
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
exec python -m dell_servicetag_mcp --transport http --host 0.0.0.0 --port "${PORT:-8000}"
