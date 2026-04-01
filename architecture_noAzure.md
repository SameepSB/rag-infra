# Agentic RAG Platform — Architecture (Local vs Production)

This document describes the architecture of the Agentic RAG platform as demonstrated in `demo_example_noAzure.ipynb`, and highlights the differences between the **local (no-Azure)** implementation and the **production (Azure-backed)** deployment.

---

## High-Level Architecture

```mermaid
graph TB
    subgraph User Layer
        U[User / Browser]
    end

    subgraph Interface Layer
        UI[Streamlit Chat UI]
    end

    subgraph Agent Layer
        SRE[SRE Agent]
        ENG[Engineering Agent]
    end

    subgraph Tool Layer
        SEARCH[Search Tool<br>RAG Retrieval]
        PG[Postgres Tool<br>Incidents & Dependencies]
        REDIS[Redis Tool<br>Conversation History]
        SANDBOX[Sandbox Tool<br>Code Execution]
    end

    subgraph Service Layer
        RAG[RAG Service<br>Ingestion & Retrieval]
        INGEST[Ingestion Service<br>Document Chunking]
    end

    subgraph Backend Layer
        LLM[LLM Provider]
        VECTORDB[Vector Store / Search Index]
        DB[(Relational Database)]
        CACHE[(Cache / Session Store)]
        DOCS[(Document Storage)]
    end

    U --> UI
    UI --> SRE
    UI --> ENG

    SRE --> SEARCH
    SRE --> PG
    SRE --> REDIS
    SRE --> SANDBOX
    ENG --> SEARCH
    ENG --> PG
    ENG --> REDIS
    ENG --> SANDBOX

    SEARCH --> RAG
    RAG --> VECTORDB
    RAG --> LLM
    INGEST --> DOCS
    INGEST --> VECTORDB

    PG --> DB
    REDIS --> CACHE
    SRE --> LLM
    ENG --> LLM
```

---

## Request Flow — Agent Execution Pipeline

```mermaid
sequenceDiagram
    participant U as User
    participant UI as Streamlit
    participant A as Agent (SRE / Eng)
    participant R as Redis Tool
    participant RAG as RAG Service
    participant VS as Vector Store
    participant PG as Postgres Tool
    participant DB as SQLite / PostgreSQL
    participant SB as Sandbox
    participant LLM as LLM (OpenAI)

    U->>UI: Send message
    UI->>A: Route to agent

    A->>R: 1. Get conversation history
    R-->>A: History (last 10 turns)

    A->>RAG: 2. Retrieve context
    RAG->>VS: Query (semantic search)
    VS-->>RAG: Top-K chunks + sources
    RAG-->>A: Context string + sources

    A->>PG: 3. DB lookup (incidents / deps)
    PG->>DB: SQL query
    DB-->>PG: Rows
    PG-->>A: Structured results

    opt Code block in message
        A->>SB: 4. Execute code
        SB-->>A: Output / error
    end

    A->>LLM: 5. Build prompt + invoke
    LLM-->>A: Generated answer

    A->>R: 6. Save to history
    A->>PG: 7. Log interaction (audit)

    A-->>UI: Response (answer, sources, tool_calls)
    UI-->>U: Display result
```

---

## Component Comparison — Local vs Production

```mermaid
graph LR
    subgraph "Local Implementation (No Azure)"
        L_UI[Streamlit Chat UI<br>No Auth]
        L_LLM[LangChain ChatOpenAI<br>Standard OpenAI API Key]
        L_VS[(ChromaDB<br>PersistentClient<br>all-MiniLM-L6-v2)]
        L_DB[(SQLite<br>demo_local.db)]
        L_CACHE[In-Memory Dict<br>LocalRedis class]
        L_DOCS[Local Directory<br>data/raw-docs/]
        L_EMB[ChromaDB Default<br>all-MiniLM-L6-v2<br>384 dimensions]
    end

    subgraph "Production Implementation (Azure)"
        P_UI[FastAPI<br>Azure Entra ID JWT]
        P_LLM[Azure OpenAI<br>Managed Identity]
        P_VS[(Azure AI Search<br>Hybrid: Keyword + Vector<br>Semantic Ranking)]
        P_DB[(Azure PostgreSQL<br>Flexible Server)]
        P_CACHE[(Azure Cache for Redis<br>SSL Port 6380)]
        P_DOCS[Azure Blob Storage<br>raw-docs container]
        P_EMB[Azure OpenAI Embeddings<br>text-embedding-3-large<br>3072 dimensions]
    end

    L_UI -.->|replaces| P_UI
    L_LLM -.->|replaces| P_LLM
    L_VS -.->|replaces| P_VS
    L_DB -.->|replaces| P_DB
    L_CACHE -.->|replaces| P_CACHE
    L_DOCS -.->|replaces| P_DOCS
    L_EMB -.->|replaces| P_EMB
```

---

## Data Flow — Ingestion Pipeline

```mermaid
flowchart LR
    subgraph Source
        LOCAL_DIR[data/raw-docs/<br>.md .txt files]
        BLOB[Azure Blob Storage<br>raw-docs container]
    end

    subgraph Chunking
        SPLIT[RecursiveCharacterTextSplitter<br>chunk_size=800<br>overlap=120]
    end

    subgraph Embedding
        LOCAL_EMB[ChromaDB DefaultEF<br>all-MiniLM-L6-v2<br>384-d vectors]
        AZURE_EMB[Azure OpenAI<br>text-embedding-3-large<br>3072-d vectors]
    end

    subgraph Storage
        CHROMA[(ChromaDB<br>PersistentClient<br>data/chroma_rag/)]
        AI_SEARCH[(Azure AI Search<br>HNSW index<br>Semantic config)]
    end

    LOCAL_DIR -->|local| SPLIT
    BLOB -->|production| SPLIT
    SPLIT -->|local| LOCAL_EMB
    SPLIT -->|production| AZURE_EMB
    LOCAL_EMB --> CHROMA
    AZURE_EMB --> AI_SEARCH
```

---

## Detailed Differences

### 1. LLM Provider

| Aspect | Local (No Azure) | Production (Azure) |
|---|---|---|
| **Client** | `langchain_openai.ChatOpenAI` | `openai.AzureOpenAI` |
| **Auth** | Standard OpenAI API key (`sk-...`) | Azure Managed Identity or API key |
| **Endpoint** | `https://api.openai.com/v1` (default) | `https://<name>.openai.azure.com` |
| **Model** | `gpt-4o` (OpenAI model name) | `gpt-4o` (Azure deployment name) |
| **API Version** | Not applicable | `2024-05-01-preview` |
| **Invocation** | `llm.invoke([messages])` → LangChain | `openai_client.chat.completions.create()` |
| **Temperature** | 0.2 (configurable via `.env`) | 0.2 (SRE) / 0.3 (Engineering) |

### 2. Vector Store / Search

| Aspect | Local (No Azure) | Production (Azure) |
|---|---|---|
| **Technology** | ChromaDB (PersistentClient) | Azure AI Search |
| **Storage** | `data/chroma_rag/` (SQLite-backed) | Azure-managed cloud index |
| **Embeddings** | `DefaultEmbeddingFunction` (all-MiniLM-L6-v2, 384-d) | Azure OpenAI `text-embedding-3-large` (3072-d) |
| **Search Type** | Approximate nearest neighbour (ANN) | Hybrid: keyword + vector + semantic ranking |
| **Chunking** | `RecursiveCharacterTextSplitter` (800 chars, 120 overlap) | Same chunking logic |
| **Index Schema** | ChromaDB collection with metadata | Fields: id, content, title, source, content_vector |
| **Embedding Cost** | Free (runs locally via sentence-transformers) | Pay-per-token via Azure OpenAI |

### 3. Database (PostgreSQL vs SQLite)

| Aspect | Local (No Azure) | Production (Azure) |
|---|---|---|
| **Technology** | SQLite (`data/demo_local.db`) | Azure Database for PostgreSQL Flexible Server |
| **Connection** | `sqlite3.connect()` (file-based) | `asyncpg` / `psycopg2` with SSL |
| **Auth** | No auth (local file) | Azure Key Vault secret (`Postgres-AdminPassword`) |
| **Schema** | Same 3 tables | Same 3 tables |
| **Tables** | `incidents`, `service_dependencies`, `agent_interactions` | Same |
| **Concurrency** | `check_same_thread=False` | Native PostgreSQL concurrency |

```
Tables (identical schema):
┌─────────────────────┐  ┌────────────────────────┐  ┌──────────────────────┐
│ incidents            │  │ service_dependencies   │  │ agent_interactions   │
├─────────────────────┤  ├────────────────────────┤  ├──────────────────────┤
│ id (PK)             │  │ id (PK)                │  │ id (PK)              │
│ service             │  │ upstream               │  │ session_id           │
│ severity            │  │ downstream             │  │ agent                │
│ title               │  │ dependency_type        │  │ query                │
│ started_at          │  │                        │  │ answer               │
│ resolved_at         │  │                        │  │ created_at           │
│ summary             │  │                        │  │                      │
└─────────────────────┘  └────────────────────────┘  └──────────────────────┘
```

### 4. Cache / Session Store (Redis)

| Aspect | Local (No Azure) | Production (Azure) |
|---|---|---|
| **Technology** | `LocalRedis` class (Python dict) | Azure Cache for Redis |
| **Storage** | `dict[str, str]` in memory | Redis server (SSL, port 6380) |
| **Auth** | None | Azure Key Vault secret (`Redis-PrimaryKey`) |
| **TTL** | Ignored (stored indefinitely until process exits) | 3600s (1 hour) enforced by Redis |
| **Persistence** | Lost on restart | Redis persistence (AOF/RDB) |
| **Operations** | `get()`, `setex()`, `ping()` | Same interface via `redis-py` |
| **History Limit** | Last 20 turns per session | Same |
| **Key Pattern** | `session:{prefix}:{session_id}:history` | Same |

### 5. Document Storage

| Aspect | Local (No Azure) | Production (Azure) |
|---|---|---|
| **Technology** | Local filesystem (`data/raw-docs/`) | Azure Blob Storage |
| **Container** | Directory on disk | Blob container (`raw-docs`) |
| **File Types** | `.md`, `.txt` | `.md`, `.txt`, `.pdf` |
| **Access** | Direct file I/O (`Path.read_text()`) | `BlobServiceClient` with Managed Identity |
| **Upload** | Copy files to directory / Streamlit uploader | Streamlit uploader / API call |

### 6. API / User Interface

| Aspect | Local (No Azure) | Production (Azure) |
|---|---|---|
| **Framework** | Streamlit | FastAPI |
| **Auth** | None | Azure Entra ID (AAD) JWT validation |
| **Endpoints** | Streamlit tabs (Chat, Ingest, Health) | `/chat`, `/ingest`, `/documents`, `/health` |
| **Session** | Streamlit `session_state` | JWT `session_id` claim |
| **Deployment** | `streamlit run streamlit_app_noAzure.py` | Azure Container Apps (Docker) |

### 7. Sandbox Tool

| Aspect | Local (No Azure) | Production (Azure) |
|---|---|---|
| **Implementation** | Identical — restricted `exec()` | Identical — restricted `exec()` |
| **Allowed builtins** | 18 safe builtins (print, len, range, etc.) | Same |
| **Restrictions** | No imports, no file I/O, no network | Same |
| **Timeout** | 10 seconds | 10 seconds |

> The Sandbox Tool is the **only component with zero differences** between local and production.

---

## Agent Architecture

```mermaid
flowchart TB
    subgraph "SRE Agent"
        SRE_IN[User Message] --> SRE_HIST[1. Load History<br>Redis Tool]
        SRE_HIST --> SRE_RAG[2. RAG Retrieval<br>ChromaDB / AI Search]
        SRE_RAG --> SRE_INC[3. Incident Lookup<br>SQLite / PostgreSQL]
        SRE_INC --> SRE_SB{Code block<br>detected?}
        SRE_SB -->|Yes| SRE_EXEC[4. Sandbox Exec]
        SRE_SB -->|No| SRE_BUILD[5. Build Prompt]
        SRE_EXEC --> SRE_BUILD
        SRE_BUILD --> SRE_LLM[6. LLM Call<br>ChatOpenAI / Azure OpenAI]
        SRE_LLM --> SRE_SAVE[7. Save History + Audit Log]
    end

    subgraph "Engineering Agent"
        ENG_IN[User Message] --> ENG_HIST[1. Load History<br>Redis Tool]
        ENG_HIST --> ENG_RAG[2. RAG Retrieval<br>ChromaDB / AI Search]
        ENG_RAG --> ENG_DEP[3. Dependency Lookup<br>SQLite / PostgreSQL]
        ENG_DEP --> ENG_SB{Code block<br>detected?}
        ENG_SB -->|Yes| ENG_EXEC[4. Sandbox Exec]
        ENG_SB -->|No| ENG_BUILD[5. Build Prompt]
        ENG_EXEC --> ENG_BUILD
        ENG_BUILD --> ENG_LLM[6. LLM Call<br>ChatOpenAI / Azure OpenAI]
        ENG_LLM --> ENG_SAVE[7. Save History + Audit Log]
    end
```

### Key Agent Differences

| Step | SRE Agent | Engineering Agent |
|---|---|---|
| **Step 3** | `query_incident_history(service)` — looks up past incidents | `query_service_dependencies(service)` — looks up dependency graph |
| **System Prompt** | RCA, remediation, triage, runbook lookup, postmortem | Architecture, code review, debugging, best practices |
| **LLM Temperature** | 0.2 (more deterministic for ops) | 0.2 local / 0.3 production (slightly more creative) |

---

## Deployment Topology

```mermaid
graph TB
    subgraph "Local (No Azure)"
        direction TB
        L_USER[User Browser] --> L_ST[Streamlit<br>localhost:8501]
        L_ST --> L_OPENAI[OpenAI API<br>api.openai.com]
        L_ST --> L_CHROMA[ChromaDB<br>data/chroma_rag/]
        L_ST --> L_SQLITE[SQLite<br>data/demo_local.db]
        L_ST --> L_DICT[In-Memory Dict<br>Python process]
        L_ST --> L_FS[Local Files<br>data/raw-docs/]
    end

    subgraph "Production (Azure)"
        direction TB
        P_USER[User / Client] --> P_ENTRA[Azure Entra ID<br>JWT Auth]
        P_ENTRA --> P_ACA[Azure Container Apps<br>FastAPI]
        P_ACA --> P_AOAI[Azure OpenAI<br>Managed Identity]
        P_ACA --> P_SEARCH[Azure AI Search<br>Hybrid + Semantic]
        P_ACA --> P_PG[Azure PostgreSQL<br>Flexible Server]
        P_ACA --> P_REDIS[Azure Cache for Redis<br>SSL:6380]
        P_ACA --> P_BLOB[Azure Blob Storage<br>raw-docs container]
        P_ACA --> P_KV[Azure Key Vault<br>Secrets]
    end
```

---

## Configuration Comparison

```
# ─── Local .env (minimal) ───────────────────
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o
LLM_TEMPERATURE=0.2

# ─── Production .env (full Azure) ───────────
AZURE_CLIENT_ID=<managed-identity-client-id>
AZURE_SEARCH_ENDPOINT=https://<search>.search.windows.net
AZURE_OPENAI_ENDPOINT=https://<openai>.openai.azure.com
AZURE_OPENAI_API_KEY=<api-key>
AZURE_OPENAI_DEPLOYMENT=gpt-4o
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-large
AZURE_OPENAI_API_VERSION=2024-05-01-preview
BLOB_URI=https://<storage>.blob.core.windows.net/raw-docs
KEYVAULT_URI=https://<vault>.vault.azure.net
POSTGRES_HOST=<host>.postgres.database.azure.com
POSTGRES_DB=ragdb
POSTGRES_USER=postgres
REDIS_HOST=<name>.redis.cache.windows.net
ENTRA_TENANT_ID=<tenant-id>
ENTRA_AUDIENCE=api://<app-id>
```

---

## Summary

The local implementation replaces **7 Azure services** with lightweight local substitutes while preserving identical agent logic, tool interfaces, and data schemas. The Sandbox Tool is the only component that is completely unchanged between environments.

| Azure Service Replaced | Local Substitute | Key Trade-off |
|---|---|---|
| Azure OpenAI | Standard OpenAI via LangChain | No Managed Identity; uses API key directly |
| Azure AI Search | ChromaDB (PersistentClient) | No hybrid/semantic ranking; smaller embeddings (384-d vs 3072-d) |
| Azure PostgreSQL | SQLite | No concurrency; no SSL; file-based |
| Azure Cache for Redis | Python dict (`LocalRedis`) | No TTL enforcement; no persistence; lost on restart |
| Azure Blob Storage | Local filesystem | No cloud access; manual file placement |
| Azure Entra ID + FastAPI | Streamlit (no auth) | No authentication or RBAC |
| Azure Key Vault | Direct env vars | Secrets in plaintext `.env` file |
