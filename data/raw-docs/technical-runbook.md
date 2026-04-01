# AgenticRAG — Operations Runbook

## Service Health Check
- Endpoint: GET /health
- Expected response: `{"status": "ok"}`
- Check frequency: every 60 seconds
- Alert threshold: 3 consecutive failures

## Deployment
1. Push code to main branch
2. GitHub Actions triggers acr-build-push workflow
3. Docker image is built and pushed to ACR
4. terraform-apply workflow deploys new image to Container Apps
5. Verify health check returns ok

## Scaling
- Min replicas: 1
- Max replicas: 3
- Scale trigger: HTTP requests > 10 concurrent

## Incident Response

### Redis Down
1. Check firewall rules: `az redis firewall-rules list`
2. Verify container outbound IP matches firewall rule
3. Restart container app revision

### AI Search Forbidden
1. Check RBAC roles on Search service
2. Verify Managed Identity has Search Index Data Reader role
3. Check local_authentication_enabled = true on Search service

### PostgreSQL Connection Failed
1. Check pg_password in Key Vault
2. Verify firewall rules allow container outbound IP
3. Check SSL mode is set to require

## Key Resources
- Container App: agentic-rag-dev-api
- Resource Group: agentic-rag-dev-rg
- Key Vault: agenticragdevkvq7g5ye
- Search Service: agentic-rag-dev-search-q7g5ye
- Redis: agentic-rag-dev-redis-q7g5ye
