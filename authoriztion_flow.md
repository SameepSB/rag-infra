# Agentic RAG — Authorization Flow
## GitHub → GitHub Actions → Terraform → Azure (Deployment & Ingestion)

> **Repo:** `SameepSB/rag-infra` | **Branch:** `azure-mvp`

---

## Table of Contents

1. [The 4 Trust Boundaries](#1-the-4-trust-boundaries)
2. [Master Flow Diagram](#2-master-flow-diagram)
3. [Phase 1 — GitHub to GitHub Actions](#3-phase-1--github-to-github-actions)
4. [Phase 2 — GitHub Actions to Azure (OIDC)](#4-phase-2--github-actions-to-azure-oidc)
5. [Phase 3 — Terraform to Azure (ARM + Entra)](#5-phase-3--terraform-to-azure-arm--entra)
6. [Phase 4 — Ingestion Authorization Flow](#6-phase-4--ingestion-authorization-flow)
7. [Phase 5 — Container App to Backend Services (Managed Identity)](#7-phase-5--container-app-to-backend-services-managed-identity)
8. [What Each Secret & Variable Authorizes](#8-what-each-secret--variable-authorizes)
9. [Authorization Chain Summary](#9-authorization-chain-summary)
10. [Troubleshooting Auth Failures by Phase](#10-troubleshooting-auth-failures-by-phase)

---

## 1. The 4 Trust Boundaries

```
┌────────────┐     ┌──────────────────┐     ┌───────────────┐     ┌──────────────┐
│   GitHub   │────►│  GitHub Actions  │────►│   Terraform   │────►│    Azure     │
│  (Repo)    │     │  (Runner/Jobs)   │     │  (IaC Engine) │     │  (Resources) │
└────────────┘     └──────────────────┘     └───────────────┘     └──────────────┘

   Boundary 1          Boundary 2               Boundary 3           Boundary 4
  Repo Secrets      OIDC Token Exchange       ARM API calls       Managed Identity
  & Permissions     (no passwords!)           + Entra Graph       (passwordless)
```

| # | Boundary | Protocol | Key Mechanism |
|---|---|---|---|
| 1 | GitHub → GitHub Actions | GitHub internal | Repo Secrets + `id-token: write` permission |
| 2 | GitHub Actions → Azure | **OIDC (OpenID Connect)** | Federated credential — no stored password |
| 3 | Terraform → Azure | Azure ARM REST API | Short-lived OIDC access token from boundary 2 |
| 4 | Container App → Azure services | **Managed Identity** | Passwordless, certificate-based by Azure fabric |

---

## 2. Master Flow Diagram

```
YOU (Human)
    │
    │  trigger workflow manually
    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  GITHUB (github.com/SameepSB/rag-infra)                                    │
│                                                                             │
│  Secrets (encrypted):          Variables (plaintext):                      │
│  ├─ AZURE_CLIENT_ID            ├─ TF_LOCATION = "South India"              │
│  ├─ AZURE_TENANT_ID            ├─ TF_PROJECT  = "agentic-rag"              │
│  ├─ AZURE_SUBSCRIPTION_ID      ├─ TF_ENV      = "dev"                      │
│  ├─ TFSTATE_RG                 ├─ TF_ADMIN_OBJECT_IDS = "<your OID>"       │
│  ├─ TFSTATE_SA                 └─ TF_RAGUSER_CLIENT_IDS = "<sp client_id>" │
│  └─ TFSTATE_CONTAINER                                                      │
│                                                                             │
│  Workflow permissions:                                                      │
│  ├─ id-token: write   ← allows OIDC JWT generation                         │
│  └─ contents: read    ← allows checkout                                    │
└───────────────────────────────────┬─────────��───────────────────────────────┘
                                    │  workflow_dispatch (manual trigger)
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  GITHUB ACTIONS RUNNER  (ubuntu-latest, ephemeral)                         │
│                                                                             │
│  Step 1: Checkout repo                                                      │
│  Step 2: azure/login@v2                                                     │
│          │                                                                  │
│          │  ① Runner requests OIDC JWT from GitHub token endpoint           │
│          │     subject = "repo:SameepSB/rag-infra:ref:refs/heads/azure-mvp"│
│          │                                                                  │
│          │  ② GitHub signs JWT with its private key                         │
│          │                                                                  │
│          │  ③ Runner POSTs JWT to Entra ID token endpoint:                  │
│          │     POST /oauth2/v2.0/token                                      │
│          │     grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer       │
│          │     client_assertion = <GitHub OIDC JWT>                         │
│          │     client_id        = AZURE_CLIENT_ID                           │
│          │                                                                  │
│          │  ④ Entra validates:                                               │
│          │     - JWT signature (GitHub's public key)                        │
│          │     - subject matches federated credential                       │
│          │       ("repo:SameepSB/rag-infra:ref:refs/heads/azure-mvp")       │
│          │     - issuer = https://token.actions.githubusercontent.com       │
│          │                                                                  │
│          │  ⑤ Entra issues: Azure AD Access Token (1hr)                     │
│          │     scopes: ARM + Entra Graph                                    │
│          │                                                                  │
│  Step 3: Terraform init                                                     │
│          Uses: TFSTATE_SA / TFSTATE_RG / TFSTATE_CONTAINER (secrets)       │
│          Auth: ARM token from step 2 → read/write tfstate blob              │
│                                                                             │
│  Step 4: Terraform plan/apply                                               │
│          Uses: ARM token → calls Azure Resource Manager REST API            │
│          Uses: ARM token → calls Microsoft Graph API (Entra resources)      │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │  ARM REST API calls (HTTPS)
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  AZURE (Microsoft Azure)                                                    │
│                                                                             │
│  Terraform creates/updates:                                                 │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  ENTRA ID (Microsoft Graph)                                         │   │
│  │  ├─ App Registration: agentic-rag-dev-api                           │   │
│  │  ├─ Service Principal                                               │   │
│  │  ├─ App Roles: sre, engineer                                        │   │
│  │  ├─ App Role Assignments (api_mi_sre, api_self_sre, raguser_*)      │   │
│  │  └─ Client Secret → stored in Key Vault as "EntraApp-ClientSecret"  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  AZURE RESOURCES (ARM)                                              │   │
│  │  ├─ Resource Group, Key Vault, Storage Account                      │   │
│  │  ├─ Container Registry, Container Apps Environment                  │   │
│  │  ├─ Azure OpenAI, AI Search, PostgreSQL, Redis                      │   │
│  │  ├─ Managed Identity (agentic-rag-dev-api-mi)                       │   │
│  │  └─ RBAC Role Assignments (see Phase 5)                             │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Phase 1 — GitHub to GitHub Actions

**What authorizes the workflow to run?**

```
GitHub Repository
        │
        │  Trigger: workflow_dispatch (manual button click)
        │            OR: push to azure-mvp branch
        │
        ▼
GitHub Actions checks:
  ✓ Does the repo have Actions enabled?
    → Settings → Actions → General → "Allow all actions"
  ✓ Does the workflow have correct permissions?
    → permissions: id-token: write   (OIDC JWT generation)
    → permissions: contents: read    (git checkout)
  ✓ Are all referenced secrets present?
    → AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID
    → TFSTATE_RG, TFSTATE_SA, TFSTATE_CONTAINER

Secrets are injected as env vars into the runner — never exposed in logs.
```

**Relevant workflow config (`bootstrap-infra.yml`, `terraform-apply.yml`):**
```yaml
permissions:
  id-token: write   # ← CRITICAL: without this, OIDC token cannot be generated
  contents: read

env:
  ARM_USE_OIDC:        "true"
  ARM_CLIENT_ID:       ${{ secrets.AZURE_CLIENT_ID }}
  ARM_TENANT_ID:       ${{ secrets.AZURE_TENANT_ID }}
  ARM_SUBSCRIPTION_ID: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
```

---

## 4. Phase 2 — GitHub Actions to Azure (OIDC)

**The core security mechanism — no stored passwords.**

```
GitHub Actions Runner
        │
        │  azure/login@v2 action
        │
        ▼
① Request OIDC token from GitHub
   ──────────────────────────────────────────────────
   GET $ACTIONS_ID_TOKEN_REQUEST_URL
   Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN

   GitHub responds with a signed JWT:
   {
     "iss": "https://token.actions.githubusercontent.com",
     "sub": "repo:SameepSB/rag-infra:ref:refs/heads/azure-mvp",
     "aud": "api://AzureADTokenExchange",
     "repository": "SameepSB/rag-infra",
     "ref":        "refs/heads/azure-mvp"
   }
   ──────────────────────────────────────────────────

② Exchange GitHub JWT for Azure Access Token
   ──────────────────────────────────────────────────
   POST https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token

   Body:
     grant_type        = urn:ietf:params:oauth:grant-type:jwt-bearer
     client_id         = AZURE_CLIENT_ID          ← from GitHub Secret
     client_assertion  = <GitHub OIDC JWT>         ← from step ①
     client_assertion_type = urn:ietf:params:oauth:client-assertion-type:jwt-bearer
     scope             = https://management.azure.com/.default
   ──────────────────────────────────────────────────

③ Entra ID validates:
   ┌─────────────────────────────────────────────────────────────┐
   │  a. JWT signature valid? (GitHub public key)                │
   │  b. issuer == "https://token.actions.githubusercontent.com" │
   │  c. subject == federated credential subject                 │
   │     "repo:SameepSB/rag-infra:ref:refs/heads/azure-mvp"     │
   │  d. audience == "api://AzureADTokenExchange"                │
   └─────────────────────────────────────────────────────────────┘
        │
        │ All pass ✅
        ▼
④ Entra issues Azure AD Access Token
   - Valid for ~1 hour
   - Scopes: ARM + Graph
   - Used by: Terraform, az CLI, ACR login
```

**Federated Credential (configured in Step 3 of DEPLOYMENT_GUIDE.md):**
```json
{
  "name": "github-branch",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:SameepSB/rag-infra:ref:refs/heads/azure-mvp",
  "audiences": ["api://AzureADTokenExchange"]
}
```

> ⚠️ If the branch name doesn't match exactly, step ③c fails → `401 Unauthorized`.

---

## 5. Phase 3 — Terraform to Azure (ARM + Entra)

**How Terraform authenticates and what it's authorized to do.**

```
Terraform (running on GitHub Actions runner)
        │
        │  Uses ARM access token from Phase 2 (via env vars)
        │  ARM_USE_OIDC = "true"
        │  ARM_CLIENT_ID / ARM_TENANT_ID / ARM_SUBSCRIPTION_ID
        │
        ▼
Terraform Init
  ──────────────────────────────────────────────────
  Reads tfstate from Azure Blob Storage:
    Resource Group : TFSTATE_RG     (GitHub Secret)
    Storage Account: TFSTATE_SA     (GitHub Secret)
    Container      : TFSTATE_CONTAINER (GitHub Secret)
    Blob key       : SameepSB/rag-infra-main.tfstate

  Auth: ARM token → Storage Blob Data Contributor
        (GitHub Actions SP has this role on tfstate SA)
  ──────────────────────────────────────────────────

Terraform Plan / Apply
  ──────────────────────────────────────────────────
  Provider: azurerm  → calls ARM REST API
            azuread  → calls Microsoft Graph API

  Roles required on GitHub Actions SP:
  ┌─────────────────────────────────────────────────┐
  │  Contributor            → create all resources  │
  │  User Access Admin      → create role assignments│
  │  Directory Readers      → read Entra SP/app data │
  └─────────────────────────────────────────────────┘

  What Terraform creates in Azure:
  ┌──────────────────────────────────────────────────────────────────┐
  │  ENTRA ID (via azuread provider):                                │
  │  ├─ azuread_application.api          App Registration            │
  │  ├─ azuread_service_principal.api    Service Principal           │
  │  ├─ azuread_application_password     Client Secret               │
  │  ├─ azuread_app_role_assignment.*    sre role assignments        │
  │                                                                  │
  │  KEY VAULT (via azurerm provider):                               │
  │  ├─ azurerm_key_vault.kv             Create vault                │
  │  ├─ azurerm_key_vault_access_policy.tf      GH Actions SP access │
  │  ├─ azurerm_key_vault_access_policy.admins  Human admin access   │
  │  └─ azurerm_key_vault_secret.api_client_secret  Write secret     │
  │                                                                  │
  │  RBAC (via azurerm provider):                                    │
  │  ├─ admin_storage           → Human admin: Storage Contributor   │
  │  ├─ deployer_storage_blob   → GH Actions SP: Storage Contributor │
  │  ├─ api_acr_pull            → MI: ACR Pull                       │
  │  ├─ api_storage_blob_reader → MI: Blob Reader                    │
  │  ├─ api_search_*            → MI: Search roles                   │
  │  └─ api_openai_*            → MI: Cognitive Services User        │
  └──────────────────────────────────────────────────────────────────┘
  ──────────────────────────────────────────────────
```

---

## 6. Phase 4 — Ingestion Authorization Flow

**The most complex chain — 5 hops of authorization.**

This happens automatically at the end of `terraform-apply` workflow (Step 10).

```
terraform-apply workflow
        │
        │  Step 10: "Wait for API health and trigger ingestion"
        │
        ▼

HOP 1 ── GitHub Actions → Key Vault
  ──────────────────────────────────────────────────────────────────
  GitHub Actions SP uses ARM token (from Phase 2) to:
  az keyvault secret show --vault-name $KV_NAME --name "EntraApp-ClientSecret"

  Auth: azurerm_key_vault_access_policy.tf
        GH Actions SP OID has: secret:Get, Set, List, Delete, Purge
  ──────────────────────────────────────────────────────────────────
        │
        │  APP_CLIENT_SECRET retrieved ✅
        ▼

HOP 2 ── GitHub Actions → Entra ID (Token Request)
  ──────────────────────────────────────────────────────────────────
  POST https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token
    grant_type    = client_credentials
    client_id     = APP_CLIENT_ID     (Entra app's own client ID)
    client_secret = APP_CLIENT_SECRET (retrieved from KV above)
    scope         = APP_CLIENT_ID/.default

  Entra validates:
    ✓ client_id exists as App Registration
    ✓ client_secret matches
    ✓ Entra issues JWT with roles: ["sre"]
      (because azuread_app_role_assignment.api_self_sre assigns
       the sre role to the app's own SP)
  ──────────────────────────────────────────────────────────────────
        │
        │  Bearer TOKEN with roles:["sre"] ✅
        ▼

HOP 3 ── Token Role Verification (in workflow)
  ──────────────────────────────────────────────────────────────────
  Decode JWT payload:
    roles = ["sre"] ✅ → proceed
    roles = []      ❌ → wait 60s, retry (Entra propagation lag)

  If still empty after retry → workflow fails
  ──────────────────────────────────────────────────────────────────
        │
        │  sre role confirmed ✅
        ▼

HOP 4 ── GitHub Actions → Container App API (/ingest)
  ──────────────────────────────────────────────────────────────────
  POST https://$FQDN/ingest
    Authorization: Bearer $TOKEN
    Body: { "container":"raw-docs", "blob_prefix":"", "force_reindex":false }

  Container App (app/core/auth.py) validates:
    ✓ Bearer token present
    ✓ JWT signature valid (RS256, Microsoft public key)
    ✓ aud == ENTRA_AUDIENCE (container app env var)
    ✓ exp not expired
    ✓ roles contains "sre" (require_role("sre","engineer"))
  ──────────────────────────────────────────────────────────────────
        │
        │  API accepts request ✅
        ▼

HOP 5 ── Container App → Blob Storage (read docs) + AI Search (write index)
  ──────────────────────────────────────────────────────────────────
  Container App Managed Identity (agentic-rag-dev-api-mi):

  → Azure Blob Storage (raw-docs container)
    Auth: DefaultAzureCredential → ManagedIdentityCredential
    Role: Storage Blob Data Reader
    (azurerm_role_assignment.api_storage_blob_data_reader)

  ��� Azure OpenAI (generate embeddings)
    Auth: DefaultAzureCredential → ManagedIdentityCredential
    Role: Cognitive Services OpenAI User
    (azurerm_role_assignment.api_openai_cognitive_services_user)

  → Azure AI Search (write chunks to index)
    Auth: DefaultAzureCredential → ManagedIdentityCredential
    Role: Search Index Data Contributor
    (azurerm_role_assignment.api_search_index_data_contributor)
  ──────────────────────────────────────────────────────────────────
        │
        │  Documents chunked, embedded, indexed ✅
        ▼

  Response: { "status": "completed", "documents_indexed": N }
```

---

## 7. Phase 5 — Container App to Backend Services (Managed Identity)

**At runtime — every API call the app makes to Azure services.**

```
Container App Process
        │
        │  from azure.identity import DefaultAzureCredential
        │  credential = DefaultAzureCredential()
        │    └─ Checks env var AZURE_CLIENT_ID
        │    └─ Uses UserAssignedManagedIdentityCredential
        │    └─ Calls Azure Instance Metadata Service (IMDS) internally
        │       GET http://169.254.169.254/metadata/identity/oauth2/token
        │           ?client_id=AZURE_CLIENT_ID
        │           &resource=https://vault.azure.net
        │
        ▼
Azure Fabric issues token for the MI automatically
(certificate-based, no password, rotated automatically)

        │
        ├──► Key Vault  (at startup — fetch secrets)
        │    Secret: Postgres-AdminPassword  → DB connection
        │    Secret: Redis-PrimaryKey        → Redis connection
        │    Secret: AzureOpenAI-ApiKey      → fallback API key
        │    Policy: azurerm_key_vault_access_policy
        │            object_id = MI principal ID
        │            permissions: secret:Get, List
        │
        ├──► Azure OpenAI  (every /chat request)
        │    Role: Cognitive Services OpenAI User
        │    Scope: azurerm_cognitive_account.openai.id
        ��
        ├──► Azure AI Search  (every /chat + /ingest request)
        │    Role: Search Index Data Reader (read)
        │    Role: Search Index Data Contributor (write/ingest)
        │    Role: Search Service Contributor
        │    Scope: azurerm_search_service.search.id
        │
        └──► Azure Blob Storage  (every /ingest + /documents request)
             Role: Storage Blob Data Reader
             Scope: azurerm_storage_account.sa.id
```

---

## 8. What Each Secret & Variable Authorizes

| Name | Type | Where Stored | Authorizes |
|---|---|---|---|
| `AZURE_CLIENT_ID` | Secret | GitHub | GitHub Actions SP identity — used in OIDC exchange with Entra |
| `AZURE_TENANT_ID` | Secret | GitHub | Entra ID tenant — token endpoint URL |
| `AZURE_SUBSCRIPTION_ID` | Secret | GitHub | ARM API scope — which subscription Terraform manages |
| `TFSTATE_RG` | Secret | GitHub | Resource group of tfstate storage account |
| `TFSTATE_SA` | Secret | GitHub | Storage account where `.tfstate` blob lives |
| `TFSTATE_CONTAINER` | Secret | GitHub | Blob container for tfstate |
| `TF_ADMIN_OBJECT_IDS` | Variable | GitHub | Your OID → KV access policy + Storage Blob Contributor role |
| `TF_RAGUSER_CLIENT_IDS` | Variable | GitHub | GitHub Actions SP client_id → sre app role assignment |
| `EntraApp-ClientSecret` | KV Secret | Azure Key Vault | Used by CI/scripts to get Bearer tokens for API calls |

---

## 9. Authorization Chain Summary

### Deployment (bootstrap-infra + terraform-apply)

```
You (click Run workflow)
  └─► GitHub validates repo permissions
        └─► GitHub Actions: id-token:write → generates OIDC JWT
              └─► Entra ID validates federated credential → issues ARM token
                    └─► Terraform uses ARM token → creates all Azure resources
                          └─► Terraform assigns all RBAC roles + Entra app roles
```

### Ingestion (terraform-apply Step 10, or manual)

```
GitHub Actions SP (ARM token from deployment)
  └─► Key Vault: GET EntraApp-ClientSecret
        └─► Entra ID: client_credentials grant → issues JWT with roles:["sre"]
              └─► Container App /ingest: validates JWT (RS256 + audience + role)
                    └─► Managed Identity → Blob Storage (read docs)
                    └─► Managed Identity → Azure OpenAI (embed)
                    └─► Managed Identity → AI Search (write index)
```

### Chat (end user / API consumer)

```
User fetches token:
  App ClientSecret (from KV) → Entra → JWT with roles:["sre"]
    └─► Container App /chat: validates JWT
          └─► Managed Identity → AI Search (retrieve chunks)
          └─► Managed Identity → Azure OpenAI (generate answer)
          └─► Managed Identity → Redis (store session)
          └─► Managed Identity → PostgreSQL (log audit)
```

---

## 10. Troubleshooting Auth Failures by Phase

| Phase | Error | Root Cause | Fix |
|---|---|---|---|
| Phase 1 | Workflow won't trigger | Actions disabled or `id-token:write` missing | Enable Actions; add permissions block |
| Phase 2 | `AADSTS70021: No matching federated identity record` | Federated credential subject doesn't match branch | Recreate federated credential with correct branch name |
| Phase 2 | `AADSTS700016: Application not found` | Wrong `AZURE_CLIENT_ID` secret | Verify client ID matches the OIDC app registration |
| Phase 3 | `AuthorizationFailed` on Terraform resource | SP missing `Contributor` role | `az role assignment create --role Contributor` |
| Phase 3 | `InsufficientPrivileges` on role assignments | SP missing `User Access Administrator` | Add User Access Administrator role to SP |
| Phase 3 | `Insufficient privileges to complete the operation` | SP missing `Directory Readers` | Assign Directory Readers in Entra ID portal |
| Phase 3 | KV `already exists` error on 2nd deploy | Soft-delete — KV still exists from previous deploy | Re-run workflow — import step handles this |
| Phase 4 | `invalid_client` on token request | Wrong client_id or expired secret | Check `EntraApp-ClientSecret` in KV; taint + recreate |
| Phase 4 | `roles: []` in token | `api_self_sre` assignment not propagated | Wait 2-5 min; workflow auto-retries once |
| Phase 4 | `401 Unauthorized` on `/ingest` | Token expired or wrong audience | Re-fetch token; check `ENTRA_AUDIENCE` env var on Container App |
| Phase 4 | `403 Forbidden` on `/ingest` | Missing `sre` role in token | Re-run bootstrap-infra to re-apply role assignments |
| Phase 5 | `ManagedIdentityCredential authentication failed` | Wrong `AZURE_CLIENT_ID` env var on Container App | Check Terraform applied correctly; verify MI client ID |
| Phase 5 | `403` on Key Vault at startup | KV access policy not created for MI | Re-run bootstrap-infra |

---

*Last updated: 2026-03-23*  
*Source: `SameepSB/rag-infra` — `azure-mvp` branch*
