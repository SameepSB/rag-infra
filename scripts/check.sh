#!/bin/bash
# ── Full diagnostic for degraded Container App ────────────────────────────────

RG="agentic-rag-dev-rg"
APP="agentic-rag-dev-api"
#PG_SERVER="agentic-rag-dev-pg-ky8yfd"
PG_SERVER=$(az postgres flexible-server list     --resource-group "$RG" --query "[0].name" -o tsv)
REDIS_NAME=$(az redis list --resource-group "$RG" --query "[0].name" -o tsv)
CAE_NAME="agentic-rag-dev-cae"

echo "════════════════════════════════════════════"
echo "1. CONTAINER APP STATUS"
echo "════════════════════════════════════════════"
az containerapp show \
	  --name "$APP" --resource-group "$RG" \
	    --query "{status:properties.runningStatus, image:properties.template.containers[0].image, replicas:properties.template.scale}" \
	      -o json

echo ""
echo "════════════════════════════════════════════"
echo "2. REVISION STATUS"
echo "════════════════════════════════════════════"
az containerapp revision list \
	  --name "$APP" --resource-group "$RG" \
	    --query "[].{name:name, active:properties.active, state:properties.runningState, replicas:properties.replicas}" \
	      -o table

echo ""
echo "════════════════════════════════════════════"
echo "3. STARTUP LOGS (last 100 lines)"
echo "════════════════════════════════════════════"
az containerapp logs show \
	  --name "$APP" --resource-group "$RG" \
	    --tail 100 --follow false 2>&1

echo ""
echo "════════════════════════════════════════════"
echo "4. HEALTH ENDPOINT"
echo "════════════════════════════════════════════"
FQDN=$(az containerapp show \
	  --name "$APP" --resource-group "$RG" \
	    --query "properties.latestRevisionFqdn" -o tsv)
echo "FQDN: $FQDN"
curl -s --max-time 10 "https://$FQDN/health" | python3 -m json.tool || echo "Health endpoint unreachable"

echo ""
echo "════════════════════════════════════════════"
echo "5. CONTAINER APP OUTBOUND IP"
echo "════════════════════════════════════════════"
az containerapp env show \
	  --name "$CAE_NAME" --resource-group "$RG" \
	    --query "properties.staticIp" -o tsv

echo ""
echo "════════════════════════════════════════════"
echo "6. POSTGRES FIREWALL RULES"
echo "════════════════════════════════════════════"
az postgres flexible-server firewall-rule list \
	  --name "$PG_SERVER" --resource-group "$RG" -o table 2>/dev/null || echo "PG firewall check failed"

echo ""
echo "════════════════════════════════════════════"
echo "7. REDIS FIREWALL RULES"
echo "════════════════════════════════════════════"
az redis firewall-rules list \
	  --name "$REDIS_NAME" --resource-group "$RG" -o table 2>/dev/null || echo "Redis firewall check failed"

echo ""
echo "════════════════════════════════════════════"
echo "DONE — paste full output above for analysis"
echo "════════════════════════════════════════════"
