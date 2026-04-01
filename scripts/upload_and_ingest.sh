#!/bin/bash
# ════════════════════════════════════════════════════════════════════
# upload_and_ingest.sh — Upload docs to Blob Storage + trigger ingestion
#
# Usage:
#   export SP_CLIENT_ID=<your-sp-client-id>
#   export SP_CLIENT_SECRET=<your-sp-secret>
#   ./upload_and_ingest.sh ./documents/
#
# Optional overrides:
#   export RG=agentic-rag-dev-rg
#   export CONTAINER=raw-docs
#   export BLOB_PREFIX=""              # scope ingestion to a subfolder
#   export FORCE_REINDEX=false
# ════════════════════════════════════════════════════════════════════
set -euo pipefail

DOCS_DIR="${1:-.}"
CONTAINER="${CONTAINER:-raw-docs}"
BLOB_PREFIX="${BLOB_PREFIX:-}"
FORCE_REINDEX="${FORCE_REINDEX:-false}"

RG="agentic-rag-dev-rg"
APP="agentic-rag-dev-api"
TENANT_ID=$(az account show --query tenantId -o tsv)
PG_SERVER=$(az postgres flexible-server list     --resource-group "$RG" --query "[0].name" -o tsv)
REDIS_NAME=$(az redis list --resource-group "$RG" --query "[0].name" -o tsv)
KV_NAME=$(az keyvault list --resource-group "$RG" --query "[0].name" -o tsv)
CAE_NAME="agentic-rag-dev-cae"
FQDN=$(az containerapp show --name "$APP" --resource-group "$RG" --query "properties.latestRevisionFqdn" -o tsv)
echo "FQDN: $FQDN"

# ── 1. Resolve Azure resource names ───────────────────────────────────────────
echo "▶ Resolving Azure resource names..."

SA_NAME=$(az storage account list \
  --resource-group "$RG" --query "[0].name" -o tsv)


# Get the app client ID from the running container's env var (most reliable)
APP_CLIENT_SECRET=$(az keyvault secret show --vault-name "$KV_NAME" --name "EntraApp-ClientSecret" --query "value" -o tsv)
APP_CLIENT_ID=$(az ad app list --query "[?displayName=='agentic-rag-dev-api'].appId" -o tsv)

echo "  Storage       : $SA_NAME"
echo "  API FQDN      : https://$FQDN"
echo "  Tenant ID     : $TENANT_ID"
echo "  App Client ID : $APP_CLIENT_ID"

# ── 2. Upload documents ────────────────────────────────────────────────────────
echo ""
echo "▶ Uploading documents from '$DOCS_DIR' to Blob Storage ($CONTAINER)..."

STORAGE_KEY=$(az storage account keys list \
  --account-name "$SA_NAME" \
  --resource-group "$RG" \
  --query "[0].value" -o tsv)

UPLOADED=0
for PATTERN in "*.pdf" "*.txt" "*.md"; do
  COUNT=$(find "$DOCS_DIR" -maxdepth 1 -name "$PATTERN" 2>/dev/null | wc -l | tr -d ' ')
  if [ "$COUNT" -gt "0" ]; then
    echo "  Uploading $COUNT $PATTERN file(s)..."
    az storage blob upload-batch \
      --account-name "$SA_NAME" \
      --account-key "$STORAGE_KEY" \
      --destination "$CONTAINER" \
      --source "$DOCS_DIR" \
      --overwrite \
      --pattern "$PATTERN" \
      --output table
    UPLOADED=$((UPLOADED + COUNT))
  fi
done

if [ "$UPLOADED" -eq "0" ]; then
  echo "  ⚠ No .pdf / .txt / .md files found in '$DOCS_DIR'"
  echo "  Continuing to trigger ingestion of any previously uploaded files..."
fi

# ── 3. Get Entra ID token via client credentials ──────────────────────────────
echo ""
echo "▶ Acquiring Entra ID token..."


TOKEN_RESPONSE=$(curl -s -X POST \
  "https://login.microsoftonline.com/${TENANT_ID}/oauth2/v2.0/token" \
  --data-urlencode "grant_type=client_credentials" \
  --data-urlencode "client_id=${APP_CLIENT_ID}" \
  --data-urlencode "client_secret=${APP_CLIENT_SECRET}" \
  --data-urlencode "scope=${APP_CLIENT_ID}/.default")

# Check for error in token response
TOKEN_ERROR=$(echo "$TOKEN_RESPONSE" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('error_description', ''))" 2>/dev/null || true)

if [ -n "$TOKEN_ERROR" ]; then
  echo "  ❌ Token acquisition failed:"
  echo "  $TOKEN_ERROR"
  echo ""
  echo "  Common causes:"
  echo "    - SP does not have the 'sre' app role assigned on the agentic-rag-dev-api app"
  echo "    - APP_CLIENT_ID is wrong (got: $APP_CLIENT_ID)"
  echo "    - SP_CLIENT_SECRET has expired"
  exit 1
fi

TOKEN=$(echo "$TOKEN_RESPONSE" | python3 -c \
  "import sys,json; print(json.load(sys.stdin)['access_token'])")
echo "  Token acquired ✅"

# ── 4. Trigger ingestion ───────────────────────────────────────────────────────
echo ""
echo "▶ Triggering ingestion..."
echo "  container     : $CONTAINER"
echo "  blob_prefix   : '${BLOB_PREFIX}'"
echo "  force_reindex : $FORCE_REINDEX"

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "https://$FQDN/ingest" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"container\": \"$CONTAINER\",
    \"blob_prefix\": \"$BLOB_PREFIX\",
    \"force_reindex\": $FORCE_REINDEX
  }")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | head -n -1)

if [ "$HTTP_CODE" != "200" ]; then
  echo "  ❌ Ingest API returned HTTP $HTTP_CODE:"
  echo "$BODY"
  echo ""
  echo "  Check container logs:"
  echo "  az containerapp logs show --name $APP --resource-group $RG --tail 50 --follow false"
  exit 1
fi

echo "$BODY" | python3 -m json.tool

STATUS=$(echo "$BODY"    | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))")
INDEXED=$(echo "$BODY"   | python3 -c "import sys,json; print(json.load(sys.stdin).get('documents_indexed',0))")
ERR_COUNT=$(echo "$BODY" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('errors',[])))")

echo ""
echo "══════════════════════════════════════"
echo "  Upload   : $UPLOADED file(s) → Blob"
echo "  Indexed  : $INDEXED chunk(s) → AI Search"
echo "  Errors   : $ERR_COUNT"
echo "  Status   : $STATUS"
echo "══════════════════════════════════════"

if [ "$STATUS" = "completed" ]; then
  echo "✅ Done — documents are searchable via POST /chat"
else
  echo "⚠ Finished with errors — check logs:"
  echo "  az containerapp logs show --name $APP --resource-group $RG --tail 50 --follow false"
  exit 1
fi
