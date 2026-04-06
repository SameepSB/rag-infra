# Agentic RAG Platform — Local Architecture & Tech Stack

This document describes the architecture and technology stack of the **local (no-Azure) implementation** of the Agentic RAG platform.

---

## High-Level Architecture

```mermaid
graph TB
    subgraph User Layer
        U[User / Browser]
    end

    subgraph Interface Layer
        UI[Streamlit Chat UI<br>localhost:8501]
    end

    subgraph Agent Layer
        SRE[SRE Agent]
        ENG[Engineering Agent]
    end

    subgraph Tool Layer
        SEARCH[Search Tool<br>ChromaDB Vector Search]
        PG[Postgres Tool<br>SQLite — Incidents & Dependencies]
        REDIS[Redis Tool<br>In-Memory Dict — Conversation History]
        SANDBOX[Sandbox Tool<br>Restricted Python Execution]
    end

    subgraph Service Layer
        RAG[RAG Service<br>Ingestion & Retrieval]
        INGEST[Ingestion Service<br>Document Chunking]
    end

    subgraph Backend Layer
        LLM[OpenAI API<br>via LangChain ChatOpenAI]
        VECTORDB[(ChromaDB<br>PersistentClient)]
        DB[(SQLite<br>demo_local.db)]
        CACHE[In-Memory Dict<br>LocalRedis class]
        DOCS[Local Directory<br>data/raw-docs/]
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
    participant R as LocalRedis (Dict)
    participant RAG as RAG Service
    participant VS as ChromaDB
    participant PG as Postgres Tool
    participant DB as SQLite
    participant SB as Sandbox
    participant LLM as OpenAI (LangChain)

    U->>UI: Send message
    UI->>A: Route to agent

    A->>R: 1. Get conversation history
    R-->>A: History (last 10 turns)

    A->>RAG: 2. Retrieve context
    RAG->>VS: Semantic search (all-MiniLM-L6-v2)
    VS-->>RAG: Top-K chunks + sources
    RAG-->>A: Context string + sources

    A->>PG: 3. DB lookup (incidents / deps)
    PG->>DB: SQL query
    DB-->>PG: Rows
    PG-->>A: Structured results

    opt Code block in message
        A->>SB: 4. Execute code in sandbox
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

## Tech Stack & Components

### 1. LLM — OpenAI via LangChain

| Aspect | Detail |
|---|---|
| **Library** | `langchain-openai` (`ChatOpenAI`) |
| **Auth** | Standard OpenAI API key (`OPENAI_API_KEY` in `.env`) |
| **Model** | `gpt-4o` (configurable via `OPENAI_MODEL`) |
| **Temperature** | `0.2` (configurable via `LLM_TEMPERATURE`) |
| **Invocation** | `llm.invoke([SystemMessage, HumanMessage, ...])` |

### 2. Vector Store — ChromaDB

| Aspect | Detail |
|---|---|
| **Library** | `chromadb` (`PersistentClient`) |
| **Storage path** | `data/chroma_rag/` (SQLite-backed on disk) |
| **Embeddings** | `DefaultEmbeddingFunction` — all-MiniLM-L6-v2 (384 dimensions) |
| **Search type** | Approximate nearest neighbour (ANN) |
| **Embedding cost** | Free — runs locally via `sentence-transformers` |
| **Chunking** | `RecursiveCharacterTextSplitter` (800 chars, 120 overlap) |

### 3. Relational Database — SQLite

| Aspect | Detail |
|---|---|
| **Library** | Python built-in `sqlite3` |
| **File** | `data/demo_local.db` |
| **Auth** | None (local file) |
| **Tables** | `incidents`, `service_dependencies`, `agent_interactions` |

```
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

### 4. Session Store — In-Memory Dict

| Aspect | Detail |
|---|---|
| **Implementation** | `LocalRedis` class (Python `dict[str, str]`) |
| **Operations** | `get()`, `setex()`, `ping()` |
| **History limit** | Last 20 turns per session |
| **Key pattern** | `session:{prefix}:{session_id}:history` |
| **Persistence** | None — lost on process restart |

### 5. Document Storage — Local Filesystem

| Aspect | Detail |
|---|---|
| **Directory** | `data/raw-docs/` |
| **Supported types** | `.md`, `.txt` |
| **Upload methods** | Copy files to directory, or use the Streamlit Ingest tab |

### 6. User Interface — Streamlit

| Aspect | Detail |
|---|---|
| **Framework** | Streamlit |
| **Auth** | None |
| **Tabs** | Chat, Ingest Documents, Health |
| **Launch command** | `streamlit run notebooks/streamlit_app_noAzure.py` |
| **Default URL** | `http://localhost:8501` |

### 7. Sandbox Tool — Restricted Python Execution

| Aspect | Detail |
|---|---|
| **Implementation** | Restricted `exec()` with allowlisted builtins |
| **Allowed builtins** | 18 safe functions: `print`, `len`, `range`, `enumerate`, `zip`, `list`, `dict`, `set`, `tuple`, `str`, `int`, `float`, `bool`, `type`, `isinstance`, `min`, `max`, `sum` |
| **Restrictions** | No imports, no file I/O, no network access |
| **Timeout** | 10 seconds |

---

## Agent Architecture

Both agents follow the same 7-step pipeline, differing only in their system prompt and which database tool they invoke at step 3.

```mermaid
flowchart TB
    IN[User Message] --> HIST[1. Load History<br>LocalRedis]
    HIST --> RAG[2. RAG Retrieval<br>ChromaDB]
    RAG --> DB_LOOKUP{3. DB Lookup}
    DB_LOOKUP -->|SRE Agent| INC[query_incident_history]
    DB_LOOKUP -->|Eng Agent| DEP[query_service_dependencies]
    INC --> SB_CHECK{Code block<br>in message?}
    DEP --> SB_CHECK
    SB_CHECK -->|Yes| EXEC[4. Sandbox Exec]
    SB_CHECK -->|No| BUILD[5. Build Prompt]
    EXEC --> BUILD
    BUILD --> LLM[6. LLM Call<br>ChatOpenAI]
    LLM --> SAVE[7. Save History + Audit Log]
```

| | SRE Agent | Engineering Agent |
|---|---|---|
| **Focus** | Incident triage, RCA, runbook lookup, postmortem | Architecture, code review, debugging, best practices |
| **DB Tool** | `query_incident_history(service)` | `query_service_dependencies(service)` |
| **System Prompt** | Operations / reliability oriented | Software design / code quality oriented |

---

## Deployment Topology

```mermaid
graph TB
    USER[User Browser] --> ST[Streamlit<br>localhost:8501]
    ST --> OPENAI[OpenAI API<br>api.openai.com]
    ST --> CHROMA[ChromaDB<br>data/chroma_rag/]
    ST --> SQLITE[SQLite<br>data/demo_local.db]
    ST --> DICT[In-Memory Dict<br>Python process]
    ST --> FS[Local Files<br>data/raw-docs/]
```

---

## Ingestion Pipeline

```mermaid
flowchart LR
    SRC[data/raw-docs/<br>.md .txt files] --> SPLIT[RecursiveCharacterTextSplitter<br>chunk_size=800<br>overlap=120]
    SPLIT --> EMB[ChromaDB DefaultEF<br>all-MiniLM-L6-v2<br>384-d vectors]
    EMB --> STORE[(ChromaDB<br>PersistentClient<br>data/chroma_rag/)]
```

---

## Configuration

The local implementation requires only three environment variables in a `.env` file at the project root:

```dotenv
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o
LLM_TEMPERATURE=0.2
```

### Additional Settings (with defaults)

| Setting | Default | Description |
|---|---|---|
| `data_dir` | `data` | Base data directory |
| `db_path` | `data/demo_local.db` | SQLite database path |
| `docs_dir` | `data/raw-docs` | Document source directory |
| `chroma_rag_dir` | `data/chroma_rag` | ChromaDB persistent storage |
| `rag_chunk_size` | `800` | Characters per chunk |
| `rag_chunk_overlap` | `120` | Overlap between chunks |
| `rag_top_k` | `5` | Number of chunks to retrieve per query |

---

## Python Dependencies

```
openai
langchain
langchain-openai
langchain-text-splitters
chromadb
pydantic
pydantic-settings
httpx
streamlit
```

Requires **Python >= 3.10**.
