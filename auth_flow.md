# Agentic RAG — Access, Authentication & Authorization Flow

> **Repo:** `SameepSB/rag-infra` | **Branch:** `azure-mvp`  
> **Identity Provider:** Microsoft Entra ID (Azure AD)  
> **Protocol:** OAuth 2.0 Client Credentials + JWT Bearer Token (RS256)

---

## Table of Contents

1. [Big Picture](#1-big-picture)
2. [Identity Components](#2-identity-components)
3. [Token Acquisition Flow](#3-token-acquisition-flow)
4. [API Authentication Flow](#4-api-authentication-flow)
5. [Authorization — Roles](#5-authorization--roles)
6. [Backend Service Authentication (Managed Identity)](#6-backend-service-authentication-managed-identity)
7. [JWT Token Anatomy](#7-jwt-token-anatomy)
8. [Endpoint Access Matrix](#8-endpoint-access-matrix)
9. [Step-by-Step: Get a Token & Call the API](#9-step-by-step-get-a-token--call-the-api)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Big Picture

```
┌──────────────────────────────────────────────────────────────────────┐
│                          CALLER                                      │
│          (Human / CI Pipeline / Service-to-Service)                  │
└─────────────────────────┬────────────────────────────────────────────┘
                          │
                          │  Step 1: Request Token
                          ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    Microsoft Entra ID                                │
│                                                                      │
│   App Registration: agentic-rag-dev-api                              │
│   Audience:         api://agentic-rag  (client_id GUID)              │
│   App Roles:        sre  |  engineer                                 │
│                                                                      │
│   Issues:  JWT (RS256, signed with Microsoft private key)            │
│   Valid:   ~1 hour                                                   │
└─────────────────────────┬────────────────────────────────────────────┘
                          │
                          │  Step 2: Bearer Token in Authorization header
                          ▼
┌──────────────────────────────────────────────────────────────────────┐
│              Agentic RAG API  (Azure Container App)                  │
│                                                                      │
│  app/core/auth.py                                                    │
│  ┌────────────────────────────────────────────────────────────┐      │
│  │  1. Extract Bearer token from Authorization header         │      │
│  │  2. Fetch Microsoft JWKS (public keys) — cached in memory  │      │
│  │  3. Verify JWT signature (RS256)                           │      │
│  │  4. Validate audience == ENTRA_AUDIENCE (client_id)        │      │
│  │  5. Check roles claim for endpoint authorization           │      │
│  └────────────────────────────────────────────────────────────┘      │
│                                                                      │
│   ✅ Valid + correct role → process request                          │
│   ❌ Missing token         → 401 Unauthorized                        │
│   ❌ Invalid/expired token → 401 Unauthorized                        │
│   ❌ Wrong/missing role    → 403 Forbidden                           │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 2. Identity Components

### Entra ID App Registration

| Property | Value |
|---|---|
| Display Name | `agentic-rag-dev-api` |
| Application ID URI | `api://agentic-rag` |
| Sign-in Audience | `AzureADMyOrg` (single tenant) |
| Token Version | v2.0 |
| Managed by | Terraform (`terraform/entra.tf`) |

### App Roles (defined in `entra.tf`)

| Role Value | Role ID | Assigned To | Purpose |
|---|---|---|---|
| `sre` | `11111111-1111-1111-1111-111111111111` | SRE team, CI pipeline, Container App MI | Full API access (chat, ingest, documents) |
| `engineer` | `22222222-2222-2222-2222-222222222222` | Engineering team | Full API access |
| `RAGUser` | `00000000-0000-0000-0000-000000000002` | *(deprecated, disabled)* | Legacy — do not use |

### Service Principals with `sre` Role (auto-assigned by Terraform)

| Principal | Terraform Resource |
|---|---|
| Container App Managed Identity | `azuread_app_role_assignment.api_mi_sre` |
| App's own Service Principal (for `client_credentials` token) | `azuread_app_role_assignment.api_self_sre` |
| GitHub Actions SP (`TF_RAGUSER_CLIENT_IDS`) | `azuread_app_role_assignment.raguser_assignments` |

---

## 3. Token Acquisition Flow

### Flow Type: OAuth 2.0 Client Credentials Grant
Used for **service-to-service** and **CI/CD** scenarios (no user interaction).

```
Caller                          Entra ID
  │                                │
  │──── POST /token ───────────────►│
  │     grant_type=client_credentials
  │     client_id=<APP_CLIENT_ID>
  │     client_secret=<SECRET>
  │     scope=<APP_CLIENT_ID>/.default
  │                                │
  │◄─── JWT Access Token ──────────│
  │     aud: <APP_CLIENT_ID>       │
  │     roles: ["sre"]             │
  │     exp: <now + 1hr>           │
  │                                │
```

### Token Endpoint

```
POST https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token
```

### Parameters

| Parameter | Value |
|---|---|
| `grant_type` | `client_credentials` |
| `client_id` | `ENTRA_AUDIENCE` (the app's own client ID) |
| `client_secret` | Value of `EntraApp-ClientSecret` from Key Vault |
| `scope` | `{APP_CLIENT_ID}/.default` |

### Shell Command

```bash
RG="agentic-rag-dev-rg"

# Collect values dynamically
TENANT_ID=$(az account show --query tenantId -o tsv)

APP_CLIENT_ID=$(az containerapp show \
  --name "agentic-rag-dev-api" \
  --resource-group "$RG" \
  --query "properties.template.containers[0].env[?name=='ENTRA_AUDIENCE'].value" \
  -o tsv)

KV_NAME=$(az keyvault list \
  --resource-group "$RG" \
  --query "[0].name" -o tsv)

APP_CLIENT_SECRET=$(az keyvault secret show \
  --vault-name "$KV_NAME" \
  --name "EntraApp-ClientSecret" \
  --query "value" -o tsv)

# Get token
TOKEN=$(curl -s -X POST \
  "https://login.microsoftonline.com/${TENANT_ID}/oauth2/v2.0/token" \
  --data-urlencode "grant_type=client_credentials" \
  --data-urlencode "client_id=${APP_CLIENT_ID}" \
  --data-urlencode "client_secret=${APP_CLIENT_SECRET}" \
  --data-urlencode "scope=${APP_CLIENT_ID}/.default" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

echo "Token: ${TOKEN:0:50}..."
```

> ⚠️ Tokens expire after **~1 hour**. Re-run the command above to get a fresh token.

---

## 4. API Authentication Flow

Implemented in `app/core/auth.py`:

```
Incoming Request
      │
      │  Authorization: Bearer eyJ0eXAiOiJKV1Q...
      ▼
┌─────────────────────────────────────────────┐
│  HTTPBearer scheme extracts token           │
│  (auto_error=False — returns None if absent)│
└─────────────────┬───────────────────────────┘
                  │
                  ▼
         Token present?
         ┌── No  ──► 401 "Missing token"
         │
         └── Yes
                  │
                  ▼
┌─────────────────────────────────────────────┐
│  Fetch Microsoft JWKS public keys           │
│  GET https://login.microsoftonline.com/     │
│      {tenant_id}/discovery/v2.0/keys        │
│  (cached in _jwks_cache — fetched once)     │
└─────────────────┬───────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────┐
│  jwt.decode(token, jwks,                    │
│    algorithms=["RS256"],                    │
│    audience=settings.entra_audience)        │
│                                             │
│  Validates:                                 │
│   ✓ Signature (RS256, Microsoft public key) │
│   ✓ Expiry (exp claim)                      │
│   ✓ Audience (aud == ENTRA_AUDIENCE)        │
│   ✓ Issuer (iss == Entra ID)                │
└─────────────────┬───────────────────────────┘
                  │
          Valid?  │
          ┌── No  ──► 401 "Invalid token"
          │
          └── Yes ──► return payload (dict of JWT claims)
                             │
                             ▼
                    Endpoint uses payload
                    (roles, appid, oid, etc.)
```

---

## 5. Authorization — Roles

Role checking is implemented via `require_role()` in `app/core/auth.py`:

```python
def require_role(*roles: str):
    async def _guard(user: dict = Depends(get_current_user)) -> dict:
        user_roles = user.get("roles", [])
        if not any(r in user_roles for r in roles):
            raise HTTPException(status_code=403, detail="Insufficient role")
        return user
    return _guard
```

### Role Check Flow

```
JWT payload (decoded)
      │
      │  { "roles": ["sre"], "aud": "...", "appid": "..." }
      ▼
require_role("sre", "engineer")
      │
      ▼
user_roles = ["sre"]
      │
any("sre" in ["sre"])  → True
      │
      └── ✅ Allowed → continue to handler

─────────────────────────────────────────���───────

JWT payload: { "roles": [], ... }
      │
any(r in [] for r ...)  → False
      │
      └── ❌ 403 Forbidden "Insufficient role"
```

### How Roles Get Into the Token

```
Terraform creates App Role in Entra:
  app_role { value = "sre", id = "1111...1111" }
          │
          ▼
Terraform assigns role to SP/MI:
  azuread_app_role_assignment { app_role_id = "1111...1111" }
          │
          ▼
Entra ID embeds role in token when SP requests with /.default scope:
  { "roles": ["sre"] }
```

---

## 6. Backend Service Authentication (Managed Identity)

The Container App uses a **User-Assigned Managed Identity** to authenticate to all backend services — **no passwords or keys** in environment variables.

```
Container App (agentic-rag-dev-api)
      │
      │  Identity: agentic-rag-dev-api-mi (User-Assigned)
      │  AZURE_CLIENT_ID env var → tells SDK which MI to use
      │
      ├──► Azure OpenAI
      │    Role: Cognitive Services OpenAI User
      │    Auth: DefaultAzureCredential → ManagedIdentityCredential
      │
      ├──► Azure AI Search
      │    Role: Search Index Data Reader + Contributor
      │    Auth: DefaultAzureCredential → ManagedIdentityCredential
      │
      ├──► Azure Blob Storage
      │    Role: Storage Blob Data Reader
      │    Auth: DefaultAzureCredential → ManagedIdentityCredential
      │
      └──► Azure Key Vault
           Policy: Get + List secrets
           Auth: DefaultAzureCredential → ManagedIdentityCredential
                 └── Fetches: Postgres-AdminPassword
                              Redis-PrimaryKey
                              AzureOpenAI-ApiKey
```

### Why `AZURE_CLIENT_ID` is Set

```bash
# In containerapps.tf
env {
  name  = "AZURE_CLIENT_ID"
  value = azurerm_user_assigned_identity.api.client_id
}
```

Without this, `DefaultAzureCredential` would not know which of potentially multiple
managed identities to use. Setting it explicitly ensures the correct MI is always selected.

---

## 7. JWT Token Anatomy

A decoded token from this system looks like:

```json
{
  "aud": "e1a6f593-7d73-4644-9231-35bd8f077afc",
  "iss": "https://login.microsoftonline.com/{tenant_id}/v2.0",
  "iat": 1711123456,
  "nbf": 1711123456,
  "exp": 1711127056,
  "appid": "e1a6f593-7d73-4644-9231-35bd8f077afc",
  "appidacr": "1",
  "idp": "https://sts.windows.net/{tenant_id}/",
  "oid": "83ebe195-21bf-47c6-82f4-eb05a03d38eb",
  "roles": [
    "sre"
  ],
  "sub": "83ebe195-21bf-47c6-82f4-eb05a03d38eb",
  "tid": "{tenant_id}",
  "ver": "2.0"
}
```

### Key Claims

| Claim | Description | Used By |
|---|---|---|
| `aud` | Audience — must equal `ENTRA_AUDIENCE` (app client ID) | `jwt.decode()` validation |
| `iss` | Issuer — Entra ID tenant URL | `jwt.decode()` validation |
| `exp` | Expiry timestamp (Unix) | `jwt.decode()` validation |
| `roles` | App roles assigned to the caller | `require_role()` authorization |
| `oid` | Object ID of the calling principal | Audit logging |
| `appid` | Client ID of the calling app | Audit logging |
| `tid` | Tenant ID | Informational |

### Decode a Token (debug)

```bash
# Decode JWT claims without verification (for debugging only)
echo "$TOKEN" \
  | cut -d. -f2 \
  | awk '{n=length($0)%4; if(n==2) print $0"=="; else if(n==3) print $0"="; else print $0}' \
  | base64 -d 2>/dev/null \
  | python3 -m json.tool
```

---

## 8. Endpoint Access Matrix

| Endpoint | Method | Auth Required | Role Required | Notes |
|---|---|---|---|---|
| `/health` | `GET` | ❌ No | None | Public health check |
| `/docs` | `GET` | ❌ No | None | Swagger UI |
| `/redoc` | `GET` | ❌ No | None | API reference |
| `/chat` | `POST` | ✅ Yes | `sre` or `engineer` | Calls SRE or Engineering agent |
| `/ingest` | `POST` | ✅ Yes | `sre` or `engineer` | Triggers document ingestion |
| `/documents` | `GET` | ✅ Yes | `sre` or `engineer` | Lists blobs in raw-docs |

---

## 9. Step-by-Step: Get a Token & Call the API

### Full Working Example

```bash
#!/bin/bash
set -e

RG="agentic-rag-dev-rg"

# ── Step 1: Collect values ─────────────────────────────────────────────────────
TENANT_ID=$(az account show --query tenantId -o tsv)

APP_CLIENT_ID=$(az containerapp show \
  --name "agentic-rag-dev-api" \
  --resource-group "$RG" \
  --query "properties.template.containers[0].env[?name=='ENTRA_AUDIENCE'].value" \
  -o tsv)

KV_NAME=$(az keyvault list \
  --resource-group "$RG" \
  --query "[0].name" -o tsv)

FQDN=$(az containerapp show \
  --name "agentic-rag-dev-api" \
  --resource-group "$RG" \
  --query "properties.latestRevisionFqdn" -o tsv)

echo "Tenant ID     : $TENANT_ID"
echo "App Client ID : $APP_CLIENT_ID"
echo "Key Vault     : $KV_NAME"
echo "API FQDN      : $FQDN"

# ── Step 2: Get client secret from Key Vault ───────────────────────────────────
APP_CLIENT_SECRET=$(az keyvault secret show \
  --vault-name "$KV_NAME" \
  --name "EntraApp-ClientSecret" \
  --query "value" -o tsv)

echo "Secret fetched ✅"

# ── Step 3: Request Bearer token from Entra ID ────────────────────────────────
TOKEN_RESPONSE=$(curl -s -X POST \
  "https://login.microsoftonline.com/${TENANT_ID}/oauth2/v2.0/token" \
  --data-urlencode "grant_type=client_credentials" \
  --data-urlencode "client_id=${APP_CLIENT_ID}" \
  --data-urlencode "client_secret=${APP_CLIENT_SECRET}" \
  --data-urlencode "scope=${APP_CLIENT_ID}/.default")

TOKEN=$(echo "$TOKEN_RESPONSE" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('access_token',''))")

if [ -z "$TOKEN" ]; then
  echo "❌ Token error:"
  echo "$TOKEN_RESPONSE" | python3 -m json.tool
  exit 1
fi
echo "Token acquired ✅"

# ── Step 4: Verify token claims ────────────────────────────────────────────────
echo ""
echo "Token claims:"
echo "$TOKEN" \
  | cut -d. -f2 \
  | awk '{n=length($0)%4; if(n==2) print $0"=="; else if(n==3) print $0"="; else print $0}' \
  | base64 -d 2>/dev/null \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('  aud   :', d.get('aud'))
print('  roles :', d.get('roles', []))
print('  exp   :', d.get('exp'))
"

# ── Step 5: Health check (no token needed) ───────────���────────────────────────
echo ""
echo "=== Health Check ==="
curl -s "https://$FQDN/health" | python3 -m json.tool

# ── Step 6: Chat with SRE agent ───────────────────────────────────────────────
echo ""
echo "=== SRE Agent ==="
curl -s -X POST "https://$FQDN/chat" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "agent": "sre",
    "session_id": "auth-test-001",
    "message": "What runbooks are available for auth-service?"
  }' | python3 -m json.tool

# ── Step 7: Chat with Engineering agent ───────────────────────────────────────
echo ""
echo "=== Engineering Agent ==="
curl -s -X POST "https://$FQDN/chat" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "agent": "engineering",
    "session_id": "auth-test-002",
    "message": "What services depend on payment-service?"
  }' | python3 -m json.tool
```

---

## 10. Troubleshooting

### Error: `401 Unauthorized — Missing token`
```
Cause  : No Authorization header sent
Fix    : Add -H "Authorization: Bearer $TOKEN" to your curl command
```

### Error: `401 Unauthorized — Invalid token`
```
Cause  : Token expired, wrong audience, or bad signature
Fix A  : Re-fetch the token (expires after ~1 hour)
Fix B  : Check aud claim matches ENTRA_AUDIENCE env var on Container App
         az containerapp show -n agentic-rag-dev-api -g agentic-rag-dev-rg \
           --query "properties.template.containers[0].env[?name=='ENTRA_AUDIENCE']"
Fix C  : Check ENTRA_TENANT_ID env var is correct on Container App
```

### Error: `403 Forbidden — Insufficient role`
```
Cause  : Token is valid but roles: [] (role not assigned or not propagated)
Fix A  : Wait 2-5 min for Entra role propagation after bootstrap-infra
Fix B  : Verify sre role is assigned to your SP:
         az rest --method GET \
           --uri "https://graph.microsoft.com/v1.0/servicePrincipals/<SP_OID>/appRoleAssignments"
Fix C  : Re-run bootstrap-infra — Terraform re-applies all role assignments
```

### Error: `invalid_client` on token request
```
Cause  : Wrong client_id or client_secret
Fix A  : Confirm APP_CLIENT_ID matches ENTRA_AUDIENCE on Container App
Fix B  : Check secret in Key Vault is not expired:
         az keyvault secret show --vault-name $KV_NAME \
           --name "EntraApp-ClientSecret" --query "attributes"
Fix C  : Taint and recreate secret:
         terraform taint azuread_application_password.api_secret
         # then re-run bootstrap-infra
```

### Error: `KV secret fetch failed` / no permission to Key Vault
```
Cause  : TF_ADMIN_OBJECT_IDS not set when bootstrap-infra ran
Fix A  : Set variable in GitHub → Settings → Variables → TF_ADMIN_OBJECT_IDS
         then re-run bootstrap-infra
Fix B  : One-time manual grant:
         MY_OID=$(az ad signed-in-user show --query id -o tsv)
         KV_NAME=$(az keyvault list -g agentic-rag-dev-rg --query "[0].name" -o tsv)
         az keyvault set-policy --name $KV_NAME --object-id $MY_OID \
           --secret-permissions get list
```

### Verify everything is wired correctly (full diagnostic)

```bash
RG="agentic-rag-dev-rg"

echo "=== Container App Env Vars ==="
az containerapp show \
  --name "agentic-rag-dev-api" \
  --resource-group "$RG" \
  --query "properties.template.containers[0].env[?name=='ENTRA_TENANT_ID' || name=='ENTRA_AUDIENCE']" \
  -o table

echo ""
echo "=== KV Secrets ==="
KV_NAME=$(az keyvault list --resource-group "$RG" --query "[0].name" -o tsv)
az keyvault secret list --vault-name "$KV_NAME" \
  --query "[].{name:name, enabled:attributes.enabled}" -o table

echo ""
echo "=== Role Assignments on App SP ==="
APP_CLIENT_ID=$(az containerapp show \
  --name "agentic-rag-dev-api" --resource-group "$RG" \
  --query "properties.template.containers[0].env[?name=='ENTRA_AUDIENCE'].value" -o tsv)
APP_SP_OID=$(az ad sp show --id "$APP_CLIENT_ID" --query id -o tsv)
az rest --method GET \
  --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$APP_SP_OID/appRoleAssignedTo" \
  --query "value[].{principal:principalDisplayName, role:appRoleId}" -o table
```

---

*Last updated: 2026-03-23*
