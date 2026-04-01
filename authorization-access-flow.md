

# Agentic RAG — Access, Roles & Authorization Explained
### *Why every identity, permission and role exists in this system*

> **Repo:** `SameepSB/rag-infra` | **Branch:** `azure-mvp`  
> This document explains **what** each identity/role is, **why** it is needed,  
> and **what breaks** if it is missing — in plain language.

---

## Table of Contents

1. [The Big Picture — Who Are the Actors?](#1-the-big-picture--who-are-the-actors)
2. [Concept: Why Not Just Use Passwords?](#2-concept-why-not-just-use-passwords)
3. [Identity 1 — The GitHub Actions Service Principal (OIDC)](#3-identity-1--the-github-actions-service-principal-oidc)
4. [Identity 2 — The Container App Managed Identity (MI)](#4-identity-2--the-container-app-managed-identity-mi)
5. [Identity 3 — The Entra App Registration](#5-identity-3--the-entra-app-registration)
6. [Identity 4 — Human Admin (You)](#6-identity-4--human-admin-you)
7. [Roles Deep Dive — Every Role Explained](#7-roles-deep-dive--every-role-explained)
8. [Key Vault — Why It Exists and Who Can Access It](#8-key-vault--why-it-exists-and-who-can-access-it)
9. [OIDC Deep Dive — How GitHub Talks to Azure Without a Password](#9-oidc-deep-dive--how-github-talks-to-azure-without-a-password)
10. [App Roles — Why the API Has Its Own Role System](#10-app-roles--why-the-api-has-its-own-role-system)
11. [The Full Authorization Map](#11-the-full-authorization-map)
12. [What Breaks If Each Access Is Missing](#12-what-breaks-if-each-access-is-missing)
13. [Glossary](#13-glossary)

---

## 1. The Big Picture — Who Are the Actors?

There are **4 actors** in this system. Each needs its own identity because each does a different job and should only have access to what it needs.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                         │
│   ACTOR 1              ACTOR 2               ACTOR 3       ACTOR 4     │
│                                                                         │
│   👤 Human Admin       🤖 GitHub Actions      🐳 Container   👥 API    │
│   (You)                Service Principal      App (API)     Consumers  │
│                                                                         │
│   "I set up and        "I deploy             "I run         "I chat    │
│    manage infra"        infrastructure        24/7 and       with the  │
│                         via Terraform"         serve users"  agents"   │
│                                                                         │
│   Identity:            Identity:             Identity:      Identity:  │
│   Your Entra           App Registration      Managed        JWT Bearer │
│   Object ID            + Service Principal   Identity       Token      │
│                         (OIDC Federated)      (passwordless) (RS256)   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

**Core principle:** Each actor gets the *minimum* access it needs — nothing more.  
This is called the **Principle of Least Privilege**.

---

## 2. Concept: Why Not Just Use Passwords?

Before explaining each identity, it's important to understand **why we don't just use passwords** everywhere.

### The Problem With Passwords in CI/CD

```
❌ The naive approach:
   Store AZURE_PASSWORD = "MyP@ssword123" in GitHub Secrets
   Use it in every workflow to log in to Azure

   Problems:
   ├─ Secret can leak in logs if not masked properly
   ├─ Secret never rotates — if stolen, attacker has permanent access
   ├─ Secret is the same for every workflow run — no audit trail
   └─ Human must manually rotate it every N days
```

```
✅ What this system does instead:

   DEPLOYMENT (GitHub Actions → Azure):
   Uses OIDC — GitHub generates a cryptographically signed, 
   short-lived (5 min), single-use JWT. Azure validates it 
   against GitHub's public key. No password stored anywhere.

   RUNTIME (Container App → Azure services):
   Uses Managed Identity — Azure fabric injects a certificate-based
   token automatically. The app never sees a password.
   Token rotates every hour automatically.

   API ACCESS (Users → Container App):
   Uses Entra JWT Bearer tokens — expire after 1 hour.
   Signed with Microsoft's private key. App validates with 
   Microsoft's public key. No password in transit.
```

---

## 3. Identity 1 — The GitHub Actions Service Principal (OIDC)

### What Is It?

An **App Registration** in Microsoft Entra ID that represents your GitHub Actions workflows. Think of it as a "robot user account" for your CI/CD pipeline.

```
Created manually (DEPLOYMENT_GUIDE.md Step 3):
  az ad app create --display-name "github-oidc-actions"
  az ad sp create --id $APP_ID
  
GitHub Secret: AZURE_CLIENT_ID = <this app's client ID>
```

### Why Does It Need a Service Principal (Not Just an App)?

- An **App Registration** defines the identity (like a user account definition)
- A **Service Principal** is the actual instantiation of that identity in the tenant  
- You cannot assign roles to an App Registration — you assign them to its Service Principal  
- Think of it like: App Registration = passport template, Service Principal = the actual passport

### Why Does It Need These 3 Roles?

```
┌──────────────────────────────────────────────────────────────────────┐
│  Role 1: Contributor                                                 │
│  Scope: /subscriptions/{subscription_id}                             │
│                                                                      │
│  WHY: Terraform needs to create, modify, and delete Azure resources  │
│  (Resource Groups, Key Vault, Storage, Container Apps, etc.)         │
│  Without this: terraform apply fails on the very first resource.     │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│  Role 2: User Access Administrator                                   │
│  Scope: /subscriptions/{subscription_id}                             │
│                                                                      │
│  WHY: Terraform doesn't just create resources — it also ASSIGNS      │
│  RBAC roles to the Managed Identity (e.g., "give this MI the         │
│  Storage Blob Data Reader role").                                     │
│  Role assignments are a privileged operation — they require this     │
│  role specifically.                                                  │
│  Without this: all azurerm_role_assignment.* in roles.tf fail.       │
└──────────────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────  ──────┐
│  Role 3: Directory Readers (Entra ID role, not Azure RBAC)           │
│  Scope: Entra ID tenant-wide                                         │
│                                                                      │
│  WHY: Terraform's azuread provider needs to look up Service          │
│  Principal Object IDs from client IDs (for TF_RAGUSER_CLIENT_IDS    │
│  variable). This requires read access to Entra directory objects.    │
│  Without this: data "azuread_service_principal" lookups fail         │
│  in entra.tf with "Insufficient privileges".                         │
└──────────────────────────────────────────────────────────────────────┘
```

### Why OIDC (Federated Credential) Instead of a Client Secret?

```
Option A — Client Secret (what most tutorials show):
  ├─ Store AZURE_CLIENT_SECRET in GitHub Secrets
  ├─ Never expires by default (or expires after years)
  ├─ If leaked: attacker can deploy/destroy your infra
  ├─ Must be manually rotated
  └─ Same secret used every run — no per-run audit trail

Option B — OIDC Federated Credential (what this system uses):
  ├─ No secret stored anywhere
  ├─ GitHub generates a 5-minute JWT per workflow run
  ├─ JWT is cryptographically bound to this specific repo + branch
  ├─ Azure validates: "is this token from repo SameepSB/rag-infra 
  │   branch azure-mvp?" — if not, rejected
  ├─ Even if JWT is intercepted, it's useless after 5 minutes
  └─ Full audit trail: every token tied to a specific workflow run ID

The Federated Credential configuration says:
  "Trust JWTs issued by GitHub for repo:SameepSB/rag-infra:ref:refs/heads/azure-mvp"
  
  This is why the branch name in the federated credential MUST match
  the branch your workflows run on. Different branch = authentication fails.
```

### What Is in GitHub Secrets vs Variables?

```
GitHub Secrets (encrypted, never shown in logs):
  AZURE_CLIENT_ID       → the SP's client ID (who is GitHub Actions?)
  AZURE_TENANT_ID       → which Entra tenant to authenticate against
  AZURE_SUBSCRIPTION_ID → which Azure subscription to manage
  TFSTATE_RG/SA/CONTAINER → where to store Terraform state

GitHub Variables (plaintext, shown in logs):
  TF_ADMIN_OBJECT_IDS      → your Object ID (safe to be visible)
  TF_RAGUSER_CLIENT_IDS    → SP client IDs (safe to be visible)
  TF_LOCATION/PROJECT/ENV  → configuration values
```

---

## 4. Identity 2 — The Container App Managed Identity (MI)

### What Is It?

A **User-Assigned Managed Identity** named `agentic-rag-dev-api-mi` that is attached to the Container App. It represents "the API application" when talking to other Azure services.

```hcl
# terraform/containerapps.tf
resource "azurerm_user_assigned_identity" "api" {
  name = "${var.project}-${var.env}-api-mi"
  ...
}

resource "azurerm_container_app" "api" {
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.api.id]
  }
  ...
  env {
    name  = "AZURE_CLIENT_ID"
    value = azurerm_user_assigned_identity.api.client_id
  }
}
```

### Why a User-Assigned MI (Not System-Assigned)?

```
System-Assigned MI:
  ├─ Created automatically when resource is created
  ├─ Deleted automatically when resource is deleted
  ├─ 1:1 tied to a single resource
  └─ Problem: if you destroy + recreate the Container App, 
     the MI gets a new Object ID → all role assignments break

User-Assigned MI:
  ├─ Created independently, lives separately from the Container App
  ├─ Can survive Container App destroy + recreate
  ├─ Role assignments stay valid
  └─ This system uses this ✅
```

### Why Does the App Need AZURE_CLIENT_ID Set?

```
When a Container App has multiple managed identities (or could have),
Azure's SDK (DefaultAzureCredential) doesn't know WHICH identity to use.

Setting AZURE_CLIENT_ID = the MI's client ID tells the SDK:
  "When you need a token, use THIS specific managed identity"

Without it:
  DefaultAzureCredential tries system-assigned MI → fails (none exists)
  Falls back to other credential types → eventually fails
  Result: app cannot talk to Key Vault, Storage, OpenAI, or Search
```

### How Does the MI Get a Token at Runtime?

```
Container App Process (Python code)
        │
        │  credential = DefaultAzureCredential()
        │  # SDK sees AZURE_CLIENT_ID env var → uses UserAssignedManagedIdentityCredential
        │
        ▼
Calls Azure Instance Metadata Service (IMDS) — internal Azure endpoint:
  GET http://169.254.169.254/metadata/identity/oauth2/token
      ?api-version=2018-02-01
      &client_id=<MI_CLIENT_ID>
      &resource=https://vault.azure.net

        │  Azure fabric (not the internet) handles this
        │  No password, no certificate needed by the app
        ▼
Returns: Access token valid for 1 hour (auto-refreshed by SDK)

The app uses this token to call:
  Key Vault   → get Postgres password, Redis key, OpenAI key
  Blob Storage → read documents from raw-docs
  AI Search   → read/write the search index
  Azure OpenAI → generate embeddings and GPT-4o responses
```

---

## 5. Identity 3 — The Entra App Registration

### What Is It?

An **Application Registration** in Entra ID named `agentic-rag-dev-api`. This defines the API's identity from an authentication perspective — it controls **who can call the API** and **what permissions they need**.

```
Think of it as: The API's "bouncer rulebook"
  ├─ What roles exist? (sre, engineer)
  ├─ Who has those roles?
  ├─ What audience must tokens target?
  └─ What client secret is used to get tokens?
```

### Why Does the App Registration Need a Client Secret?

```
The API needs to be CALLED by:
  1. Human operators (you) — to upload docs, chat, ingest
  2. GitHub Actions — to trigger ingestion automatically after deploy
  3. The app itself — to verify it can generate valid tokens (self-test)

To call the API, a caller needs a Bearer JWT token.
To get a JWT, they need to authenticate to Entra ID using:
  client_id     = the app's client ID  (public)
  client_secret = the app's secret     (private, stored in Key Vault)
  scope         = APP_CLIENT_ID/.default

The secret is stored in Key Vault as "EntraApp-ClientSecret"
and NEVER hardcoded in code or environment variables.
```

### Why Does the App Call Itself?

```
Scenario: terraform-apply finishes deploying → immediately triggers ingestion

The workflow needs a valid Bearer token to call POST /ingest.
The simplest identity to use is the app's own service principal
(azuread_app_role_assignment.api_self_sre) — because:
  ├─ Its client_id and secret are already in Key Vault
  ├─ It already has the sre role assigned
  └─ No additional identity needs to be created for CI/CD

Flow:
  Workflow fetches secret from KV (using GitHub Actions SP ARM token)
    → POSTs to Entra with app's own client_id + secret
    → Gets back JWT with roles:["sre"]
    → Uses JWT to call POST /ingest
    → API validates JWT and processes request
```

### Why Is the Audience (aud) the Client ID, Not a URL?

```
Standard OAuth2 audience = a URI like "https://myapi.example.com"
Microsoft Entra v2 tokens = audience is the APPLICATION'S CLIENT ID (a GUID)

When the app calls Entra with:
  scope = "<CLIENT_ID>/.default"

Entra sets:
  aud = "<CLIENT_ID>"  (the GUID, not a URL)

The API validates:
  jwt.decode(token, jwks, audience=settings.entra_audience)
  
  where settings.entra_audience = ENTRA_AUDIENCE env var 
                                 = azuread_application.api.client_id

This ensures tokens meant for OTHER apps cannot be used to call this API.
```

---

## 6. Identity 4 — Human Admin (You)

### What Is It?

Your personal Entra ID Object ID (OID) — a GUID that uniquely identifies your user account in the Entra tenant. Passed to Terraform via `TF_ADMIN_OBJECT_IDS`.

### Why Does Terraform Need Your OID?

```
Terraform creates resources but the resources are locked down by default.
After a fresh deploy, only the GitHub Actions SP can access Key Vault
and Storage — not you.

By passing TF_ADMIN_OBJECT_IDS = <your OID>, Terraform grants you:

  Key Vault access policy (keyvault.tf):
    azurerm_key_vault_access_policy.admins
    → secret: Get, Set, List, Delete, Purge, Recover
    → key:    Get, List
    
    WHY: So you can manually inspect secrets, debug token issues,
         and retrieve EntraApp-ClientSecret to get tokens locally.

  Storage RBAC role (storage.tf):
    azurerm_role_assignment.admin_storage
    → role: Storage Blob Data Contributor
    
    WHY: So you can upload documents to raw-docs container manually
         without needing storage account keys.
```

### What Happens If TF_ADMIN_OBJECT_IDS Is Not Set?

```
Terraform apply succeeds — all Azure resources created.

But YOU have no access to:
  ❌ Key Vault → cannot read EntraApp-ClientSecret → cannot get tokens
  ❌ Storage   → cannot upload documents manually
  
Fix: Set TF_ADMIN_OBJECT_IDS = $(az ad signed-in-user show --query id -o tsv)
     Re-run bootstrap-infra → Terraform creates the access policies
```

---

## 7. Roles Deep Dive — Every Role Explained

### Azure RBAC Roles (on Azure Resources)

| Role | Assigned To | On Resource | Why It's Needed |
|---|---|---|---|
| `Contributor` | GitHub Actions SP | Subscription | Create/update/delete all Azure resources during Terraform apply |
| `User Access Administrator` | GitHub Actions SP | Subscription | Terraform creates role assignments (requires this elevated role) |
| `Storage Blob Data Contributor` | GitHub Actions SP | Storage Account | GitHub Actions can upload docs to `raw-docs` and write `tfstate` blobs |
| `Storage Blob Data Contributor` | Human Admin OID | Storage Account | You can upload docs manually without storage account keys |
| `Storage Blob Data Reader` | Container App MI | Storage Account | API can read documents from `raw-docs` during ingestion |
| `AcrPull` | Container App MI | Container Registry | Container App can pull the Docker image from private ACR at startup |
| `Search Index Data Reader` | Container App MI | AI Search | API can query the search index during `/chat` requests |
| `Search Index Data Contributor` | Container App MI | AI Search | API can write chunks to the search index during `/ingest` |
| `Search Service Contributor` | Container App MI | AI Search | API can manage index definitions (create/update schema) |
| `Cognitive Services OpenAI User` | Container App MI | Azure OpenAI | API can call GPT-4o and text-embedding model endpoints |

### Key Vault Access Policies (not RBAC — separate system)

| Policy | Assigned To | Permissions | Why It's Needed |
|---|---|---|---|
| `azurerm_key_vault_access_policy.tf` | GitHub Actions SP | secret: Get, Set, List, Delete, Purge, Recover | Terraform writes secrets to KV (e.g. `EntraApp-ClientSecret`) during apply |
| `azurerm_key_vault_access_policy.admins` | Human Admin OID | secret: Get, Set, List, Delete, Purge, Recover / key: Get, List | You can read/manage secrets manually for debugging and token generation |
| `azurerm_key_vault_access_policy.api` | Container App MI | secret: Get, List | API reads DB password, Redis key, and OpenAI key at startup |

> **Why Key Vault uses Access Policies instead of RBAC?**  
> This system uses the older "Vault Access Policy" model (not Azure RBAC for KV).  
> Both work — this was chosen for simpler Terraform resource management.

### Entra App Roles (inside the API's own authorization system)

These roles live inside the Entra App Registration and are embedded in JWT tokens:

| Role | Role ID | Assigned To | Why It's Needed |
|---|---|---|---|
| `sre` | `1111...1111` | Container App MI | App can call its own `/ingest` and `/chat` endpoints in self-tests |
| `sre` | `1111...1111` | App's own SP | GitHub Actions can get a token with sre role to trigger ingestion after deploy |
| `sre` | `1111...1111` | GitHub Actions SP (`TF_RAGUSER_CLIENT_IDS`) | Allows CI pipeline to call API endpoints with a valid sre-role token |
| `engineer` | `2222...2222` | *(manually assigned to engineers)* | Engineers can call `/chat` and `/ingest` with engineer role in token |

**How role checking works in the API (`app/core/auth.py`):**

```python
def require_role(*roles: str):
    async def _guard(user: dict = Depends(get_current_user)) -> dict:
        user_roles = user.get("roles", [])          # from JWT claims
        if not any(r in user_roles for r in roles): # check any match
            raise HTTPException(403, "Insufficient role")
        return user
    return _guard

# Usage on endpoints:
@router.post("/ingest")
async def ingest(user = Depends(require_role("sre", "engineer"))):
    ...
```

---

## 8. Key Vault — Why It Exists and Who Can Access It

### Why Key Vault at All?

```
The system needs these secrets at runtime:
  1. PostgreSQL admin password    (long, random, must not be in code)
  2. Redis primary access key     (long, random, must not be in code)
  3. Azure OpenAI API key         (required for South India regional endpoint)
  4. Entra App client secret      (used by CI + humans to get Bearer tokens)

Option A — Store in environment variables:
  ❌ Visible in Azure Portal under Container App settings
  ❌ Visible in Terraform state file (stored in Storage Account)
  ❌ Anyone with Reader role on the Container App can see them

Option B — Store in Key Vault:
  ✅ Encrypted at rest with Microsoft-managed keys
  ✅ Access controlled by explicit policy per identity
  ✅ Full audit log of every Get/Set operation
  ✅ Secrets never appear in Terraform state (only KV secret names do)
  ✅ Container App env var only stores the SECRET NAME, not the value
```

### Access Map

```
┌──────────────────────────────   ─────────────────────────┐
│           Azure Key Vault (agentic-rag-dev-kv...)       │
│                                                        │
│  Secrets:                                              │
│  ├─ Postgres-AdminPassword                             │
│  ├─ Redis-PrimaryKey                                   │
│  ├─ AzureOpenAI-ApiKey                                 │
│  └─ EntraApp-ClientSecret                              │
│                                                        │
│  Who can access:                                       │
│                                                        │
│  GitHub Actions SP  →  Get/Set/List/Delete/Purge       │
│  (writes secrets during terraform apply)               │
│                                                        │
│  Container App MI   →  Get/List only                   │
│  (reads secrets at app startup)                        │
│                                                        │
│  Human Admin (you)  →  Get/Set/List/Delete/Purge       │
│  (manage secrets, debug, read for local testing)       │
└────────────────────────────────────────────────────────┘
```

### Why Can the GitHub Actions SP Write to Key Vault?

```
During terraform apply, Terraform:
  1. Creates the Entra App Registration
  2. Generates a client secret (azuread_application_password)
  3. Must STORE that secret somewhere secure for the app to use

The secret is written to Key Vault via:
  resource "azurerm_key_vault_secret" "api_client_secret" {
    name         = "EntraApp-ClientSecret"
    value        = azuread_application_password.api_secret.value
    key_vault_id = azurerm_key_vault.kv.id
    depends_on   = [azurerm_key_vault_access_policy.tf]
  }

Without Write access for the GitHub Actions SP → this step fails
→ Container App cannot get tokens → API cannot serve requests
```

---

## 9. OIDC Deep Dive — How GitHub Talks to Azure Without a Password

### The Core Idea

OIDC (OpenID Connect) allows two systems that **already trust a common authority** to authenticate with each other — without pre-sharing a password.

In this case:
- **GitHub** is the **Identity Provider (IdP)** — it vouches for its own workflows
- **Azure Entra ID** is configured to **trust GitHub as an IdP**
- The **Federated Credential** is the trust agreement: "trust JWTs from GitHub for this specific repo + branch"

### Step-by-Step Token Exchange

```
SETUP (done once manually):
─────────────────────────────────────────────────────────
  You configure Entra App Registration with:
  Federated Credential:
    issuer  = "https://token.actions.githubusercontent.com"
    subject = "repo:SameepSB/rag-infra:ref:refs/heads/azure-mvp"
    audience= "api://AzureADTokenExchange"
  
  This tells Entra: "Trust tokens from GitHub for this exact repo/branch"

RUNTIME (every workflow run):
─────────────────────────────────────────────────────────

  Step 1: GitHub generates a short-lived OIDC JWT (valid 5 min)
  ┌────────────────────────────────────────────────────────────┐
  │  {                                                         │
  │    "iss": "https://token.actions.githubusercontent.com",  │
  │    "sub": "repo:SameepSB/rag-infra:ref:refs/heads/azure-mvp",│
  │    "aud": "api://AzureADTokenExchange",                    │
  │    "run_id": "12345678",                                   │
  │    "repository": "SameepSB/rag-infra",                    │
  │    "ref": "refs/heads/azure-mvp",                         │
  │    "exp": <now + 5 min>                                   │
  │  }                                                         │
  │  Signed with GitHub's RSA private key                     │
  └────────────────────────────────────────────────────────────┘

  Step 2: azure/login@v2 action sends this JWT to Entra
  POST https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token
    grant_type        = urn:ietf:params:oauth:grant-type:jwt-bearer
    client_id         = AZURE_CLIENT_ID        (from GitHub Secret)
    client_assertion  = <GitHub OIDC JWT>
    scope             = https://management.azure.com/.default

  Step 3: Entra validates
  ┌────────────────────────────────────────────────────────────┐
  │  ✓ Is client_id known? (AZURE_CLIENT_ID exists in tenant)  │
  │  ✓ Is issuer trusted? (matches federated credential issuer)│
  │  ✓ Does subject match? (repo + branch exact match)         │
  │  ✓ Is JWT signature valid? (GitHub's public key)           │
  │  ✓ Is JWT not expired? (5 min window)                      │
  └────────────────────────────────────  ───────────────────────┘

  Step 4: Entra issues Azure Access Token (valid 1 hour)
  This token can call Azure ARM API + Microsoft Graph API
  Terraform uses this token for ALL resource operations
```

### Why the Branch Must Match Exactly

```
Federated credential subject: "repo:SameepSB/rag-infra:ref:refs/heads/azure-mvp"

If workflow runs on branch "main":
  GitHub JWT subject = "repo:SameepSB/rag-infra:ref:refs/heads/main"
  Entra checks: does "...main" match "...azure-mvp"? → NO
  Result: AADSTS70021 "No matching federated identity record found"

This is a SECURITY FEATURE — it prevents a workflow on any other
branch from deploying to your Azure environment.
```

---

## 10. App Roles — Why the API Has Its Own Role System

### The Problem App Roles Solve

```
Without app roles, the API would have only two options:
  Option A: No auth — anyone on the internet can call /ingest and destroy your index
  Option B: Single password — hard to rotate, no per-user audit trail

With Entra App Roles:
  ✅ Multiple roles (sre, engineer) with different permissions
  ✅ Roles are embedded in the JWT — no database lookup needed
  ✅ Token expires in 1 hour — stolen token has limited blast radius
  ✅ Full Entra audit log of who got what token when
  ✅ Can revoke access by removing role assignment (no password change needed)
```

### How Roles Flow From Entra Into the JWT

```
Terraform defines roles in the App Registration (entra.tf):
  app_role {
    value = "sre"
    id    = "11111111-1111-1111-1111-111111111111"
  }

Terraform assigns roles to identities:
  azuread_app_role_assignment.api_self_sre
    → "The app's own SP has the sre role"
  azuread_app_role_assignment.api_mi_sre
    → "The Container App MI has the sre role"
  azuread_app_role_assignment.raguser_assignments
    → "The GitHub Actions SP has the sre role"

When a caller requests a token with scope = APP_CLIENT_ID/.default:
  Entra checks: what roles does this caller have on this app?
  Entra embeds those roles in the JWT:
    { "roles": ["sre"] }

API code checks:
  user_roles = token.get("roles", [])
  if "sre" not in user_roles → 403 Forbidden
```

### Why the App's Own SP Has the sre Role (api_self_sre)

```
Scenario: terraform-apply finishes → must immediately trigger ingestion
          to index any documents in raw-docs.

The workflow already has the GitHub Actions SP ARM token (for az CLI).
But it needs an API Bearer token (JWT with sre role) to call POST /ingest.

The cleanest solution: the app authenticates AS ITSELF using its own
client_id and the EntraApp-ClientSecret already stored in Key Vault.

  GitHub Actions SP (ARM token)
    → reads EntraApp-ClientSecret from Key Vault
    → POSTs to Entra: "give me a token for app X as app X"
    → Entra checks: does the app X SP have sre role on app X? YES
    → Issues JWT with roles:["sre"]
    → Workflow calls POST /ingest with this JWT

Without api_self_sre:
  Token would have roles:[] → API returns 403 → ingestion fails
```

---

## 11. The Full Authorization Map

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    COMPLETE AUTHORIZATION MAP                                   │
│                    Agentic RAG System                                           │
└─────────────────────────────────────────────────────────────────────────────────┘

  ACTOR                 AUTHENTICATES WITH       AUTHORIZED TO ACCESS
  ─────────────────────────────────────────────────────────────────────────────────

  GitHub Actions SP     OIDC JWT (5 min)         → Azure ARM API
  (github-oidc-actions) exchanged for            → Microsoft Graph API
                        ARM Access Token          → Terraform state (Blob Storage)
                        (1 hr)                   → Key Vault (write secrets)
                                                 → ACR (push/pull images)
                                                 → Create all Azure resources
                                                 → Assign all RBAC roles
                                                 → Create Entra App + assign app roles

  Container App MI      Managed Identity          → Key Vault (read secrets only)
  (agentic-rag-dev-     (certificate, auto-       → Blob Storage (read raw-docs)
   api-mi)              rotated, internal)        → Azure OpenAI (generate text)
                                                 → AI Search (read + write index)
                                                 → ACR (pull image at startup)

  Human Admin           az login (browser SSO)    → Key Vault (full management)
  (your OID)            → ARM Access Token        → Blob Storage (upload docs)
                                                 → Azure Portal (view all resources)

  API Callers           Entra client_credentials  → POST /chat   (sre or engineer)
  (humans + CI)         JWT Bearer Token          → POST /ingest (sre or engineer)
                        (1 hr)                   → GET /documents (sre or engineer)
                        roles: ["sre"] or         → GET /health  (no auth needed)
                               ["engineer"]

  ─────────────────────────────────────────────────────────────────────────────────

  AZURE SERVICE         PROTECTED BY              WHAT ENFORCES IT
  ─────────────────────────────────────────────────────────────────────────────────

  Key Vault             Access Policies            Azure Key Vault service
  Blob Storage          RBAC (role assignments)    Azure Storage service
  AI Search             RBAC (role assignments)    Azure AI Search service
  Azure OpenAI          RBAC (role assignments)    Azure Cognitive Services
  Container Registry    RBAC (AcrPull)             Azure Container Registry
  Container App API     Entra JWT validation        app/core/auth.py (Python)
  PostgreSQL            Password (from KV)         PostgreSQL server
  Redis                 Access key (from KV)       Redis cache service
```

---

## 12. What Breaks If Each Access Is Missing

| Missing Access | Immediate
