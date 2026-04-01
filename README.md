# Agentic RAG — Complete Deployment Guide

> **Repo:** `SameepSB/rag-infra` | **Branch:** `azure-mvp`  
> **Stack:** FastAPI · LangGraph · Azure Container Apps · Azure OpenAI · Azure AI Search · PostgreSQL · Redis · Key Vault

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Create Terraform State Storage](#2-create-terraform-state-storage)
3. [Create GitHub Actions OIDC Service Principal](#3-create-github-actions-oidc-service-principal)
4. [Register Azure Resource Providers](#4-register-azure-resource-providers)
5. [Configure GitHub Repository](#5-configure-github-repository)
6. [Run Workflows in Order](#6-run-workflows-in-order)
7. [Upload Documents & Test](#7-upload-documents--test)
8. [Access via Web / Swagger UI](#8-access-via-web--swagger-ui)
9. [Test SRE & Engineering Agents](#9-test-sre--engineering-agents)
10. [Tear Down](#10-tear-down)
11. [What Terraform Manages Automatically](#11-what-terraform-manages-automatically)
12. [Known Issues & Fixes](#12-known-issues--fixes)
13. [Architecture Overview](#13-architecture-overview)

---

## 1. Prerequisites

| Tool | Minimum Version | Install |
|---|---|---|
| Azure CLI | 2.55+ | https://learn.microsoft.com/en-us/cli/azure/install-azure-cli |
| Terraform | 1.7.5 | https://developer.hashicorp.com/terraform/install |
| Docker | 24+ | https://docs.docker.com/get-docker |
| jq | any | `sudo apt install jq` / `brew install jq` |
| Python | 3.11+ | https://www.python.org/downloads |

```bash
# Verify all tools
az version
terraform version
docker version
jq --version
python3 --version
```

---

## 2. Create Terraform State Storage

> **Run once locally. Never destroy this storage account.**

```bash
# ── Login ─────────────────────────────────────────────────────────────────────
az login
az account show

SUBSCRIPTION_ID=$(az account show --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)
MY_OID=$(az ad signed-in-user show --query id -o tsv)

echo "Subscription : $SUBSCRIPTION_ID"
echo "Tenant       : $TENANT_ID"
echo "My OID       : $MY_OID"   # ← Save this — needed for TF_ADMIN_OBJECT_IDS

# ── Variables ─────────────────────────────────────────────────────────────────
RG_NAME="tfstate-rg"
LOCATION="southindia"           # Must match TF_LOCATION variable later
SA="tfstate$RANDOM$RANDOM"
SA=$(echo $SA | cut -c1-24)     # Max 24 chars, globally unique
CONTAINER="tfstate"

echo "Storage Account name: $SA"   # ← SAVE THIS for TFSTATE_SA secret

# ���─ Create resources ──────────────────────────────────────────────────────────
az group create -n "$RG_NAME" -l "$LOCATION"

az storage account create \
  -n "$SA" -g "$RG_NAME" -l "$LOCATION" \
  --sku Standard_LRS \
  --allow-blob-public-access false

az storage container create \
  --name "$CONTAINER" \
  --account-name "$SA" \
  --auth-mode login

echo "════════════════════════════════════"
echo "TFSTATE_RG        : $RG_NAME"
echo "TFSTATE_SA        : $SA"
echo "TFSTATE_CONTAINER : $CONTAINER"
echo "════════════════════════════════════"
```

---

## 3. Create GitHub Actions OIDC Service Principal

```bash
GITHUB_OWNER="SameepSB"       # ← Your GitHub username / org
GITHUB_REPO="rag-infra"       # ← Your repo name
BRANCH="azure-mvp"            # ← Your working branch

# ── Create App Registration ───────────────────────────────────────────────────
az ad app create --display-name "github-oidc-actions" > app.json
APP_ID=$(cat app.json | jq -r .appId)
echo "APP_ID (AZURE_CLIENT_ID): $APP_ID"   # ← SAVE THIS

# ── Create Service Principal ──────────────────────────────────────────────────
az ad sp create --id "$APP_ID"
SP_OBJECT_ID=$(az ad sp show --id "$APP_ID" --query id -o tsv)

# ── Assign Roles ──────────────────────────────────────────────────────────────
# Contributor — create/manage Azure resources
az role assignment create \
  --assignee "$APP_ID" \
  --role "Contributor" \
  --scope "/subscriptions/$SUBSCRIPTION_ID"

# User Access Administrator — manage role assignments in Terraform
az role assignment create \
  --assignee-object-id "$SP_OBJECT_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "User Access Administrator" \
  --scope "/subscriptions/$SUBSCRIPTION_ID"

# Directory Readers — Entra app/SP lookups in Terraform
# This command assigns a Microsoft Entra ID (Azure AD) built‑in role to the service principal, and prints a success or fallback message based on the result.
# Directory Reader's Template ID:88d8e3e3-8f55-4a1e-953a-9b9898b8876b from Azure portal

az rest --method POST \
  --uri "https://graph.microsoft.com/v1.0/directoryRoles/roleTemplateId=88d8e3e3-8f55-4a1e-953a-9b9898b8876b/members/\$ref" \
  --body "{\"@odata.id\": \"https://graph.microsoft.com/v1.0/directoryObjects/$SP_OBJECT_ID\"}" \
  2>/dev/null && echo "Directory Readers assigned ✅" \
  || echo "Directory Readers — assign manually in Azure Portal > Entra ID > Roles"

# ── Create Federated Credential (OIDC) ───────────────────────────────────────
az ad app federated-credential create \
  --id "$APP_ID" \
  --parameters "{
    \"name\": \"github-branch\",
    \"issuer\": \"https://token.actions.githubusercontent.com\",
    \"subject\": \"repo:${GITHUB_OWNER}/${GITHUB_REPO}:ref:refs/heads/${BRANCH}\",
    \"audiences\": [\"api://AzureADTokenExchange\"]
  }"

# ── Validate ──────────────────────────────────────────────────────────────────
az ad app federated-credential list --id "$APP_ID" -o table

echo "════════════════════════════════════"
echo "AZURE_CLIENT_ID       : $APP_ID"
echo "AZURE_TENANT_ID       : $TENANT_ID"
echo "AZURE_SUBSCRIPTION_ID : $SUBSCRIPTION_ID"
echo "════════════════════════════════════"
```

---

## 4. Register Azure Resource Providers

```bash
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.OperationalInsights
az provider register --namespace Microsoft.ContainerService
az provider register --namespace Microsoft.KeyVault
az provider register --namespace Microsoft.CognitiveServices
az provider register --namespace Microsoft.Search
az provider register --namespace Microsoft.DBforPostgreSQL
az provider register --namespace Microsoft.Cache
az provider register --namespace Microsoft.ContainerRegistry

# Wait for Microsoft.App (required before bootstrap)
echo "Waiting for Microsoft.App..."
while [ "$(az provider show --namespace Microsoft.App --query registrationState -o tsv)" != "Registered" ]; do
  echo "  Still registering..."; sleep 10
done
echo "Microsoft.App registered ✅"
```

---

## 5. Configure GitHub Repository

### 5a — Secrets
`Settings → Secrets and variables → Actions → Secrets → New repository secret`

| Secret Name | Value | Where to get it |
|---|---|---|
| `AZURE_CLIENT_ID` | App ID | Step 3 output |
| `AZURE_TENANT_ID` | Tenant ID | Step 1 output |
| `AZURE_SUBSCRIPTION_ID` | Subscription ID | Step 1 output |
| `TFSTATE_RG` | `tfstate-rg` | Step 2 output |
| `TFSTATE_SA` | Storage account name | Step 2 output |
| `TFSTATE_CONTAINER` | `tfstate` | Step 2 output |

### 5b — Variables
`Settings → Secrets and variables → Actions → Variables → New repository variable`

| Variable Name | Value | Description |
|---|---|---|
| `TF_LOCATION` | `South India` | Azure region for all resources |
| `TF_PROJECT` | `agentic-rag` | Project name prefix |
| `TF_ENV` | `dev` | Environment name |
| `TF_ADMIN_OBJECT_IDS` | `<YOUR_OID>` | Your OID from Step 1 — grants KV + Storage access |
| `TF_RAGUSER_CLIENT_IDS` | `<APP_ID>` | GitHub Actions SP client ID from Step 3 |

> ⚠️ `TF_ADMIN_OBJECT_IDS` is critical — without it you will not have Key Vault or Storage access after deploy.

### 5c — Workflow Permissions
`Settings → Actions → General → Workflow permissions`

```
✅ Read and write permissions
✅ Allow GitHub Actions to create and approve pull requests
```

### 5d — Enable Actions (if forked)
```
Actions tab → click "I understand my workflows, go ahead and enable them"
```

---

## 6. Run Workflows in Order

Go to **GitHub → Actions tab** and run each workflow manually in this exact sequence:

### Step 6.1 — `bootstrap-infra` (manual trigger)
Creates all Azure infrastructure:
- Resource Group, Key Vault, Storage Account
- Azure Container Registry, Container Apps Environment
- Azure OpenAI, AI Search, PostgreSQL, Redis
- Managed Identity, Entra App Registration
- All RBAC role assignments

> ⚠️ **May fail on 1st run** with `azurerm_key_vault_access_policy.api_mi already exists`.  
> This is a known race condition — just **re-run the workflow**. 2nd run always succeeds.

### Step 6.2 — `acr-build-push` (manual trigger)
Builds the FastAPI Docker image from `app/` and pushes it to ACR.

### Step 6.3 — `terraform-apply` (manual trigger)
- Deploys the Container App with the real image
- Wires all environment variables
- Waits up to 10 min for the API health check to pass

> ⚠️ **May fail on 1st run** with health timeout (cold start).  
> Re-run the workflow — 2nd run always succeeds.

---

## 7. Upload Documents & Test

### 7a — Get dynamic values

```bash
RG="agentic-rag-dev-rg"

SA_NAME=$(az storage account list \
  --resource-group "$RG" --query "[0].name" -o tsv)

KV_NAME=$(az keyvault list \
  --resource-group "$RG" --query "[0].name" -o tsv)

FQDN=$(az containerapp show \
  --name "agentic-rag-dev-api" \
  --resource-group "$RG" \
  --query "properties.latestRevisionFqdn" -o tsv)

APP_CLIENT_ID=$(az containerapp show \
  --name "agentic-rag-dev-api" \
  --resource-group "$RG" \
  --query "properties.template.containers[0].env[?name=='ENTRA_AUDIENCE'].value" \
  -o tsv)

echo "Storage Account : $SA_NAME"
echo "Key Vault       : $KV_NAME"
echo "API FQDN        : $FQDN"
echo "App Client ID   : $APP_CLIENT_ID"
```

### 7b — Upload Documents

```bash
# Using storage account key (works immediately, no RBAC wait)
SA_KEY=$(az storage account keys list \
  --account-name "$SA_NAME" \
  --resource-group "$RG" \
  --query "[0].value" -o tsv)

# Upload a single file
az storage blob upload \
  --account-name "$SA_NAME" \
  --account-key "$SA_KEY" \
  --container-name "raw-docs" \
  --name "runbook-auth-service.md" \
  --file "./runbook-auth-service.md" \
  --overwrite

# Upload an entire folder
az storage blob upload-batch \
  --account-name "$SA_NAME" \
  --account-key "$SA_KEY" \
  --destination "raw-docs" \
  --source "./documents/" \
  --overwrite

# Verify
az storage blob list \
  --account-name "$SA_NAME" \
  --account-key "$SA_KEY" \
  --container-name "raw-docs" \
  --query "[].{name:name, size:properties.contentLength}" \
  -o table
```

**Recommended folder structure:**
```
raw-docs/
  ├── runbooks/
  │   ├── runbook-auth-service.md
  │   └── runbook-payment-service.md
  ├── incidents/
  │   └── postmortem-2026-03-15.md
  ├── slos/
  │   └── service-slos.md
  └── architecture/
      └── auth-service-design.md
```

### 7c — Get Bearer Token

```bash
TENANT_ID=$(az account show --query tenantId -o tsv)

APP_CLIENT_SECRET=$(az keyvault secret show \
  --vault-name "$KV_NAME" \
  --name "EntraApp-ClientSecret" \
  --query "value" -o tsv)

TOKEN=$(curl -s -X POST \
  "https://login.microsoftonline.com/${TENANT_ID}/oauth2/v2.0/token" \
  --data-urlencode "grant_type=client_credentials" \
  --data-urlencode "client_id=${APP_CLIENT_ID}" \
  --data-urlencode "client_secret=${APP_CLIENT_SECRET}" \
  --data-urlencode "scope=${APP_CLIENT_ID}/.default" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

echo "Token acquired ✅ (${TOKEN:0:40}...)"
```

### 7d — Trigger Document Ingestion

```bash
# Ingest all documents
curl -s -X POST "https://$FQDN/ingest" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"container":"raw-docs","blob_prefix":"","force_reindex":false}' \
  | python3 -m json.tool

# Force full re-index (clears existing index)
curl -s -X POST "https://$FQDN/ingest" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"container":"raw-docs","blob_prefix":"","force_reindex":true}' \
  | python3 -m json.tool
```

**Expected response:**
```json
{
    "status": "completed",
    "documents_indexed": 6,
    "errors": []
}
```

---

## 8. Access via Web / Swagger UI

### Option A — Swagger UI (built-in, no setup needed)

Open in browser:
```
https://<YOUR_FQDN>/docs
```

Steps:
1. Click **Authorize** 🔒 (top right)
2. Enter: `Bearer <your_token>`
3. Click **Authorize → Close**
4. Expand any endpoint → **Try it out** → **Execute**

### Option B — Standalone Chat Web UI

Save the file below as `chat-ui.html` and open it in any browser — no server required.

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>Agentic RAG — Chat UI</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: system-ui, sans-serif; background: #f0f2f5; height: 100vh; display: flex; flex-direction: column; }
    header { background: #0078d4; color: white; padding: 16px 24px; display: flex; align-items: center; gap: 12px; }
    header h1 { font-size: 1.2rem; }
    .agent-toggle { margin-left: auto; display: flex; gap: 8px; }
    .agent-toggle button { padding: 6px 16px; border: 2px solid white; border-radius: 20px; background: transparent; color: white; cursor: pointer; font-weight: 600; }
    .agent-toggle button.active { background: white; color: #0078d4; }
    .config { background: white; padding: 12px 24px; border-bottom: 1px solid #ddd; display: flex; gap: 12px; align-items: center; }
    .config input { flex: 1; padding: 8px 12px; border: 1px solid #ccc; border-radius: 6px; font-size: 0.85rem; }
    .config label { font-size: 0.8rem; color: #666; white-space: nowrap; }
    .chat { flex: 1; overflow-y: auto; padding: 20px 24px; display: flex; flex-direction: column; gap: 16px; }
    .msg { max-width: 75%; padding: 12px 16px; border-radius: 12px; line-height: 1.5; font-size: 0.95rem; }
    .msg.user { align-self: flex-end; background: #0078d4; color: white; border-bottom-right-radius: 4px; }
    .msg.assistant { align-self: flex-start; background: white; border: 1px solid #ddd; border-bottom-left-radius: 4px; }
    .msg .sources { margin-top: 8px; font-size: 0.78rem; color: #666; border-top: 1px solid #eee; padding-top: 6px; }
    .msg .sources a { color: #0078d4; text-decoration: none; display: block; }
    .msg.error { background: #fde8e8; color: #c00; border: 1px solid #f5c0c0; align-self: flex-start; }
    .msg.typing { background: white; border: 1px solid #ddd; align-self: flex-start; color: #999; font-style: italic; }
    .input-area { background: white; border-top: 1px solid #ddd; padding: 16px 24px; display: flex; gap: 12px; }
    .input-area textarea { flex: 1; padding: 10px 14px; border: 1px solid #ccc; border-radius: 8px; resize: none; font-size: 0.95rem; font-family: inherit; height: 48px; }
    .input-area button { padding: 0 24px; background: #0078d4; color: white; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; }
    .input-area button:disabled { background: #aaa; cursor: not-allowed; }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 0.75rem; font-weight: 600; margin-bottom: 6px; }
    .badge.sre { background: #dbeafe; color: #1d4ed8; }
    .badge.engineering { background: #dcfce7; color: #15803d; }
  </style>
</head>
<body>
<header>
  <h1>🤖 Agentic RAG</h1>
  <div class="agent-toggle">
    <button id="btn-sre" class="active" onclick="setAgent('sre')">SRE</button>
    <button id="btn-engineering" onclick="setAgent('engineering')">Engineering</button>
  </div>
</header>
<div class="config">
  <label>API URL:</label>
  <input id="api-url" type="text" placeholder="https://your-fqdn.azurecontainerapps.io" />
  <label>Token:</label>
  <input id="token" type="password" placeholder="Paste Bearer token here" />
</div>
<div class="chat" id="chat"></div>
<div class="input-area">
  <textarea id="input" placeholder="Ask the SRE agent..." onkeydown="handleKey(event)"></textarea>
  <button id="send-btn" onclick="sendMessage()">Send</button>
</div>
<script>
  let agent = 'sre';
  let sessionId = 'session-' + Math.random().toString(36).substr(2, 9);
  window.onload = () => {
    document.getElementById('api-url').value = localStorage.getItem('api_url') || '';
    document.getElementById('token').value = localStorage.getItem('token') || '';
  };
  function setAgent(a) {
    agent = a;
    document.getElementById('btn-sre').classList.toggle('active', a === 'sre');
    document.getElementById('btn-engineering').classList.toggle('active', a === 'engineering');
    document.getElementById('input').placeholder = `Ask the ${a.toUpperCase()} agent...`;
  }
  function handleKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  }
  function addMsg(role, content, sources, error) {
    const chat = document.getElementById('chat');
    const div = document.createElement('div');
    div.className = `msg ${error ? 'error' : role}`;
    if (role === 'assistant') {
      const badge = document.createElement('div');
      badge.className = `badge ${agent}`;
      badge.textContent = agent.toUpperCase();
      div.appendChild(badge);
    }
    const text = document.createElement('div');
    text.textContent = content;
    div.appendChild(text);
    if (sources && sources.length > 0) {
      const s = document.createElement('div');
      s.className = 'sources';
      s.innerHTML = '📎 Sources:
' + sources.map(url => {
        const name = url.split('/').pop();
        return `<a href="${url}" target="_blank">• ${name}</a>`;
      }).join('');
      div.appendChild(s);
    }
    chat.appendChild(div);
    chat.scrollTop = chat.scrollHeight;
    return div;
  }
  async function sendMessage() {
    const input = document.getElementById('input');
    const msg = input.value.trim();
    if (!msg) return;
    const apiUrl = document.getElementById('api-url').value.trim().replace(/\/$/, '');
    const token = document.getElementById('token').value.trim();
    localStorage.setItem('api_url', apiUrl);
    localStorage.setItem('token', token);
    if (!apiUrl || !token) { alert('Please fill in API URL and Token first'); return; }
    input.value = '';
    document.getElementById('send-btn').disabled = true;
    addMsg('user', msg);
    const chat = document.getElementById('chat');
    const typing = document.createElement('div');
    typing.className = 'msg typing';
    typing.textContent = '⏳ Thinking...';
    chat.appendChild(typing);
    chat.scrollTop = chat.scrollHeight;
    try {
      const res = await fetch(`${apiUrl}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
        body: JSON.stringify({ agent: agent, session_id: sessionId, message: msg })
      });
      typing.remove();
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        addMsg('assistant', `❌ Error ${res.status}: ${err.detail || res.statusText}`, null, true);
      } else {
        const data = await res.json();
        addMsg('assistant', data.answer, data.sources);
      }
    } catch (e) {
      typing.remove();
      addMsg('assistant', `❌ Network error: ${e.message}`, null, true);
    }
    document.getElementById('send-btn').disabled = false;
    input.focus();
  }
</script>
</body>
</html>
```

---

## 9. Test SRE & Engineering Agents

### Health Check (no token required)

```bash
curl -s "https://$FQDN/health" | python3 -m json.tool
```

### SRE Agent Tests

```bash
# P1 incident triage
curl -s -X POST "https://$FQDN/chat" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent":"sre","session_id":"sre-001","message":"What should I check for a P1 alert on auth-service?"}' \
  | python3 -m json.tool

# Postmortem draft
curl -s -X POST "https://$FQDN/chat" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent":"sre","session_id":"sre-001","message":"Help me draft a postmortem for last nights outage"}' \
  | python3 -m json.tool

# SLO check
curl -s -X POST "https://$FQDN/chat" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent":"sre","session_id":"sre-002","message":"What is the SLO for the checkout service?"}' \
  | python3 -m json.tool
```

### Engineering Agent Tests

```bash
# Architecture question
curl -s -X POST "https://$FQDN/chat" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent":"engineering","session_id":"eng-001","message":"What design pattern does our auth service use?"}' \
  | python3 -m json.tool

# Service dependencies
curl -s -X POST "https://$FQDN/chat" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent":"engineering","session_id":"eng-001","message":"What services depend on payment-service?"}' \
  | python3 -m json.tool

# Code review
curl -s -X POST "https://$FQDN/chat" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent":"engineering","session_id":"eng-002","message":"Review this Python function for production readiness: def get_user(id): return db.query(id)"}' \
  | python3 -m json.tool
```

### Agent Reference

| Token Role | `agent` value | Designed For |
|---|---|---|
| `sre` | `"sre"` | Incidents, runbooks, postmortems, SLOs, RCA |
| `engineer` | `"engineering"` | Code review, architecture, service deps, design patterns |

### Expected Response Shape

```json
{
    "session_id": "sre-001",
    "agent": "sre",
    "answer": "Based on the runbook for auth-service, here are the steps...",
    "sources": [
        "https://<storage>.blob.core.windows.net/raw-docs/runbook-auth-service.md"
    ],
    "tool_calls": [
        "query_incident_history(service=auth)"
    ]
}
```

---

## 10. Tear Down

```
GitHub → Actions → terraform-destroy → Run workflow
  confirm input: DESTROY
```

> ⚠️ This destroys all Azure resources in `agentic-rag-dev-rg`.  
> The tfstate storage account (`tfstate-rg`) is **NOT** destroyed — it is managed separately.

---

## 11. What Terraform Manages Automatically

The following access is granted automatically by Terraform — **no manual steps required** as long as `TF_ADMIN_OBJECT_IDS` is set correctly in GitHub Variables.

| Access | Principal | Terraform Resource |
|---|---|---|
| Key Vault secrets (Get/Set/List) | Your OID (`TF_ADMIN_OBJECT_IDS`) | `azurerm_key_vault_access_policy.admins` |
| Key Vault secrets (Get/Set/List) | GitHub Actions SP | `azurerm_key_vault_access_policy.tf` |
| Storage Blob Data Contributor | Your OID (`TF_ADMIN_OBJECT_IDS`) | `azurerm_role_assignment.admin_storage` |
| Storage Blob Data Contributor | GitHub Actions SP | `azurerm_role_assignment.deployer_storage_blob_contributor` |
| Storage Blob Data Reader | Container App MI | `azurerm_role_assignment.api_storage_blob_data_reader` |
| ACR Pull | Container App MI | `azurerm_role_assignment.api_acr_pull` |
| AI Search (Read/Write) | Container App MI | `azurerm_role_assignment.api_search_*` |
| Cognitive Services OpenAI User | Container App MI | `azurerm_role_assignment.api_openai_*` |
| `sre` app role | Container App MI | `azuread_app_role_assignment.api_mi_sre` |
| `sre` app role | GitHub Actions SP (`TF_RAGUSER_CLIENT_IDS`) | `azuread_app_role_assignment.raguser_assignments` |
| `sre` app role | App's own SP | `azuread_app_role_assignment.api_self_sre` |

> If you ever lose access, the fix is always: **set `TF_ADMIN_OBJECT_IDS` and re-run `bootstrap-infra`**.

---

## 12. Known Issues & Fixes

### Issue 1 — `bootstrap-infra` fails on 1st run after destroy

**Error:**
```
Error: a resource with the ID ".../objectId/83ebe195-..." already exists
  with azurerm_key_vault_access_policy.api_mi
```

**Cause:** Azure soft-delete retains the Key Vault for 7 days. On re-deploy, the MI's access policy already exists in Azure but not in Terraform state.

**Fix:** Re-run `bootstrap-infra` — the import step handles it automatically on 2nd run.

---

### Issue 2 — `terraform-apply` health timeout on 1st run

**Error:**
```
❌ API health timeout after 10 min
```

**Cause:** Container App cold start (OpenAI model warm-up, Redis/PG connections stabilising, Entra role propagation ~2-5 min).

**Fix:** Re-run `terraform-apply` — services are warm on 2nd run.

---

### Issue 3 — Storage upload permission error

**Error:**
```
You do not have the required permissions ... Storage Blob Data Contributor
```

**Cause:** `TF_ADMIN_OBJECT_IDS` was not set when `bootstrap-infra` ran.

**Fix:**
```bash
# Option A — set variable and re-run bootstrap-infra (recommended)
# GitHub → Settings → Variables → TF_ADMIN_OBJECT_IDS = <your OID>
# Then re-run bootstrap-infra workflow

# Option B — one-time manual grant
SA_NAME=$(az storage account list --resource-group "agentic-rag-dev-rg" --query "[0].name" -o tsv)
MY_OID=$(az ad signed-in-user show --query id -o tsv)
az role assignment create \
  --role "Storage Blob Data Contributor" \
  --assignee "$MY_OID" \
  --scope $(az storage account show -n "$SA_NAME" -g "agentic-rag-dev-rg" --query id -o tsv)
```

---

### Issue 4 — Token error after fresh deploy

**Symptom:** `invalid_client` or empty token response.

**Cause:** `EntraApp-ClientSecret` not yet written to Key Vault (KV access policy race on fresh deploy).

**Fix:** Re-run `bootstrap-infra` to ensure the secret is written, then retry token fetch.

---

## 13. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        User / CI Pipeline                       │
└───────────────────────────┬─────────────────────────────────────┘
                            │  Bearer JWT (Entra ID)
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│              Azure Container App  (agentic-rag-dev-api)         │
│                                                                 │
│   POST /chat ──► SRE Agent  ──► RAG Retrieval ──► GPT-4o       │
│                  Engineering Agent ──────────────────────────   │
│   POST /ingest ──► Chunk → Embed → AI Search Index             │
│   GET  /health                                                  │
│   GET  /documents                                               │
└──────────┬──────────────────────────────────────────────────────┘
           │  Managed Identity (passwordless)
     ┌─────┴──────┐
     │            │
     ▼            ▼
Key Vault    AI Search ──► Azure OpenAI (GPT-4o + Embeddings)
     │
     ▼
PostgreSQL   Redis (session history)
     │
     ▼
Blob Storage (raw-docs)
```

### Deployed Resources

| Resource Name | Azure Service | Purpose |
|---|---|---|
| `agentic-rag-dev-api` | Container App | Hosts FastAPI + both agents |
| `agentic-rag-dev-cae` | Container Apps Environment | Secure runtime boundary |
| `agentic-rag-dev-openai` | Azure OpenAI (S0) | GPT-4o + text-embedding-3-large |
| `agentic-rag-dev-search` | Azure AI Search (Basic) | Vector + keyword hybrid index |
| `agentic-rag-dev-pg` | Azure PostgreSQL (B1ms) | Incidents, dependencies, audit log |
| `agentic-rag-dev-redis` | Azure Cache for Redis (C1) | Session memory, conversation history |
| `agentic-rag-dev-kv<suffix>` | Azure Key Vault | Secrets (PG password, Redis key, client secret) |
| `agentic-rag-dev-acr<suffix>` | Azure Container Registry | Docker image storage |
| `agentic-rag-dev-api-mi` | Managed Identity | Passwordless auth to all services |

---

*Last updated: 2026-03-23*
