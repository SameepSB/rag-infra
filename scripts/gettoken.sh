#!/bin/bash
# ══════════════════════════════════════════════════════════════════
# get_token.sh — Get Bearer token for all API calls
# Fully automated — reads APP_CLIENT_ID + SECRET from Azure directly
# No env vars needed after terraform apply
# ══════════════════════════════════════════════════════════════════
set -euo pipefail

RG="agentic-rag-dev-rg"
APP="agentic-rag-dev-api"
TENANT_ID=$(az account show --query tenantId -o tsv)
PG_SERVER=$(az postgres flexible-server list     --resource-group "$RG" --query "[0].name" -o tsv)
REDIS_NAME=$(az redis list --resource-group "$RG" --query "[0].name" -o tsv)
KV_NAME=$(az keyvault list --resource-group "$RG" --query "[0].name" -o tsv)
CAE_NAME="agentic-rag-dev-cae"
FQDN=$(az containerapp show --name "$APP" --resource-group "$RG" --query "properties.latestRevisionFqdn" -o tsv)
echo "FQDN: $FQDN"


# ── Fetch client ID + secret directly from Terraform outputs / Key Vault ──────

APP_CLIENT_SECRET=$(az keyvault secret show --vault-name "$KV_NAME" --name "EntraApp-ClientSecret" --query "value" -o tsv)
APP_CLIENT_ID=$(az ad app list --query "[?displayName=='agentic-rag-dev-api'].appId" -o tsv)



echo "API : https://$FQDN"
echo "App Client ID : $APP_CLIENT_ID"
# ── Get token ──────────────────────────────────────────────────────────────────
TOKEN=$(curl -s -X POST \
	  "https://login.microsoftonline.com/${TENANT_ID}/oauth2/v2.0/token" \
	    --data-urlencode "grant_type=client_credentials" \
	      --data-urlencode "client_id=${APP_CLIENT_ID}" \
	        --data-urlencode "client_secret=${APP_CLIENT_SECRET}" \
		  --data-urlencode "scope=${APP_CLIENT_ID}/.default" \
		    | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

echo "$TOKEN"

curl -s "https://$FQDN/health" | python3 -m json.tool
