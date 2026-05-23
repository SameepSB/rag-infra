"""
Agentic RAG Platform — Streamlit Chat UI

A browser-based chat interface for the SRE and Engineering agents.
Replaces FastAPI routes (app/api/routes/*.py) with a Streamlit multi-tab app.

Features:
  - 💬 Chat: Multi-turn conversation with SRE or Engineering agent
  - 📄 Ingest: Upload .md/.txt files into the knowledge base
  - 🩺 Health: Real-time status of all backend services
  - 📋 Audit Log: Browse recent agent interactions
  - 🗂️ History: Search and replay past conversations

Usage:
  $ cd <project-root>
  $ streamlit run rag_infra/notebooks/streamlit_app.py
"""

import sys
import os
import json
import logging
import hashlib
import traceback
import sqlite3
from pathlib import Path
from typing import Any, Optional
from enum import Enum
from datetime import datetime

import streamlit as st
from streamlit_modal import Modal
import chromadb
from chromadb.utils import embedding_functions
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ────────────────────────────────────────────────────────────────────────────
# 1. Setup & Configuration
# ────────────────────────────────────────────────────────────────────────────

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).parent.parent.parent
os.chdir(PROJECT_ROOT)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger("streamlit_app")


class Settings(BaseSettings):
    """Local configuration — reads from .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    llm_provider: str = "openai"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    googlegemini_api_key: str = ""
    googlegemini_model: str = "google.gemini"
    llm_temperature: float = 0.2

    data_dir: Path = Path("data")
    db_path: Path = Path("data/demo_local.db")
    docs_dir: Path = Path("data/raw-docs")
    chroma_rag_dir: Path = Path("data/chroma_rag")

    rag_chunk_size: int = 800
    rag_chunk_overlap: int = 120
    rag_top_k: int = 5
    chroma_embedding_function: str = "default"


@st.cache_resource
def load_settings() -> Settings:
    """Load and cache settings."""
    return Settings()


settings = load_settings()

# Ensure directories exist
for d in [settings.data_dir, settings.docs_dir, settings.chroma_rag_dir]:
    (PROJECT_ROOT / d).mkdir(parents=True, exist_ok=True)


# ────────────────────────────────────────────────────────────────────────────
# 2. Schemas
# ────────────────────────────────────────────────────────────────────────────

class AgentType(str, Enum):
    sre = "sre"
    engineering = "engineering"


class ChatRequest(BaseModel):
    agent: AgentType = AgentType.sre
    session_id: str = Field(..., description="Unique session/conversation ID")
    message: str = Field(..., min_length=1, max_length=4096)


class ChatResponse(BaseModel):
    session_id: str
    agent: AgentType
    answer: str
    sources: list[str] = []
    tool_calls: list[str] = []


# ────────────────────────────────────────────────────────────────────────────
# 3. Local Clients
# ────────────────────────────────────────────────────────────────────────────

@st.cache_resource
def init_llm() -> ChatOpenAI:
    """Initialize LangChain ChatOpenAI client."""
    if settings.llm_provider.lower() == "gemini":
        if not settings.googlegemini_api_key:
            raise ValueError("GOOGLE_GEMINI_API_KEY not set in .env")
        llm_model = settings.googlegemini_model
        llm_api_key = settings.googlegemini_api_key
    else:
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY not set in .env")
        llm_model = settings.openai_model
        llm_api_key = settings.openai_api_key

    return ChatOpenAI(
        model=llm_model,
        api_key=llm_api_key,
        temperature=settings.llm_temperature,
    )


class LocalRedis:
    """In-memory Redis substitute for conversation history."""

    def __init__(self):
        self._store: dict[str, str] = {}

    def get(self, key: str) -> Optional[str]:
        return self._store.get(key)

    def setex(self, key: str, ttl: int, value: str):
        self._store[key] = value

    def ping(self) -> bool:
        return True


@st.cache_resource
def init_redis() -> LocalRedis:
    """Initialize local Redis mock."""
    return LocalRedis()


DB_PATH = PROJECT_ROOT / settings.db_path
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def get_db() -> sqlite3.Connection:
    """Get SQLite connection."""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@st.cache_resource
def init_db():
    """Initialize SQLite schema."""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'medium',
                title TEXT NOT NULL,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved_at TIMESTAMP,
                summary TEXT
            );
            CREATE TABLE IF NOT EXISTS service_dependencies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                upstream TEXT NOT NULL,
                downstream TEXT NOT NULL,
                dependency_type TEXT NOT NULL DEFAULT 'http'
            );
            CREATE TABLE IF NOT EXISTS agent_interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                query TEXT NOT NULL,
                answer TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS conversation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # Seed incidents if empty
        existing = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
        if existing == 0:
            conn.executemany(
                "INSERT INTO incidents (service, severity, title, summary) VALUES (?, ?, ?, ?)",
                [
                    ("payment-service", "high", "Payment gateway 5xx spike",
                     "Intermittent 502s from Stripe webhook handler; root cause was connection pool exhaustion."),
                    ("auth-service", "critical", "OAuth token endpoint down",
                     "Expired TLS cert on auth-service caused 100% failures for 12 minutes."),
                    ("order-service", "medium", "Slow order confirmation emails",
                     "SQS queue lag reached 45 s due to under-provisioned consumers."),
                    ("payment-service", "low", "Minor logging noise in payment-service",
                     "Debug-level logs flooding CloudWatch; log level bumped to INFO."),
                ],
            )
        
        # Seed dependencies if empty
        existing_deps = conn.execute("SELECT COUNT(*) FROM service_dependencies").fetchone()[0]
        if existing_deps == 0:
            conn.executemany(
                "INSERT INTO service_dependencies (upstream, downstream, dependency_type) VALUES (?, ?, ?)",
                [
                    ("api-gateway", "auth-service", "http"),
                    ("api-gateway", "order-service", "http"),
                    ("order-service", "payment-service", "http"),
                    ("order-service", "inventory-service", "grpc"),
                    ("payment-service", "stripe-webhook", "http"),
                    ("notification-service", "order-service", "async/sqs"),
                ],
            )
    return True


init_db()
llm = init_llm()
redis_client = init_redis()


# ────────────────────────────────────────────────────────────────────────────
# 4. Knowledge Base Service
# ────────────────────────────────────────────────────────────────────────────

class KnowledgeBaseService:
    """ChromaDB-backed knowledge base."""

    def __init__(self, settings: Settings):
        self._settings = settings
        chroma_path = PROJECT_ROOT / settings.chroma_rag_dir
        chroma_path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(chroma_path))
        self._collection_name = "rag_knowledge_base"

        if settings.chroma_embedding_function == "default":
            self._embedding_function = embedding_functions.DefaultEmbeddingFunction()
        elif settings.chroma_embedding_function.startswith("all-"):
            self._embedding_function = embedding_functions.SBERTEmbeddingFunction(
                settings.chroma_embedding_function
            )
        else:
            self._embedding_function = embedding_functions.DefaultEmbeddingFunction()

        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            embedding_function=self._embedding_function,
        )
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.rag_chunk_size,
            chunk_overlap=settings.rag_chunk_overlap,
        )

    def ingest_directory(self, directory: Path, clear_existing: bool = False) -> dict[str, int]:
        """Chunk and upsert .md / .txt files into ChromaDB."""
        if clear_existing:
            self._client.delete_collection(name=self._collection_name)
            self._collection = self._client.get_or_create_collection(
                name=self._collection_name,
                embedding_function=self._embedding_function,
            )
        
        source_files = sorted([*directory.glob("*.md"), *directory.glob("*.txt")])
        docs, ids, metadatas = [], [], []
        
        for file_path in source_files:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
            chunks = self._splitter.split_text(text)
            for index, chunk in enumerate(chunks):
                chunk_hash = hashlib.sha1(chunk.encode("utf-8")).hexdigest()[:10]
                doc_id = f"{file_path.stem}-{index}-{chunk_hash}"
                docs.append(chunk)
                ids.append(doc_id)
                metadatas.append({"source": file_path.name, "chunk_index": index})
        
        if docs:
            self._collection.upsert(documents=docs, ids=ids, metadatas=metadatas)
        
        return {
            "files_indexed": len(source_files),
            "chunks_indexed": len(docs),
            "collection_count": self._collection.count(),
        }

    def search(self, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        """Query ChromaDB for relevant document chunks."""
        if self._collection.count() == 0:
            return []
        
        results = self._collection.query(
            query_texts=[query],
            n_results=top_k or self._settings.rag_top_k,
            include=["documents", "metadatas", "distances"],
        )
        
        documents = (results.get("documents") or [[]])[0]
        metadatas = (results.get("metadatas") or [[]])[0]
        distances = (results.get("distances") or [[]])[0]
        combined = []
        
        for i, document in enumerate(documents):
            metadata = metadatas[i] if i < len(metadatas) else {}
            distance = distances[i] if i < len(distances) else None
            combined.append({
                "content": document,
                "source": metadata.get("source", "unknown"),
                "distance": distance,
            })
        
        return combined

    @property
    def count(self) -> int:
        return self._collection.count()


@st.cache_resource
def init_kb() -> KnowledgeBaseService:
    """Initialize and cache knowledge base."""
    kb = KnowledgeBaseService(settings=settings)
    local_docs_dir = PROJECT_ROOT / settings.docs_dir
    local_docs_dir.mkdir(parents=True, exist_ok=True)
    kb.ingest_directory(local_docs_dir)
    return kb


kb = init_kb()


# ────────────────────────────────────────────────────────────────────────────
# 5. Agent Tools
# ────────────────────────────────────────────────────────────────────────────

HISTORY_TTL = 3600
CONTEXT_MAX_CHARS = 6000
SANDBOX_TIMEOUT = 10

ALLOWED_BUILTINS = {
    "print", "len", "range", "enumerate", "zip",
    "list", "dict", "set", "tuple", "str", "int",
    "float", "bool", "type", "isinstance", "min", "max", "sum",
}

_STOP_WORDS = {
    "what", "when", "where", "which", "there", "their", "about",
    "would", "could", "should", "have", "been", "that", "this",
    "with", "from", "your", "into", "will", "more", "also",
}


def retrieve_context(query: str, top_k: int = 5) -> tuple[str, list[str]]:
    """Retrieve relevant chunks from ChromaDB."""
    hits = kb.search(query, top_k=top_k)
    context_parts = []
    sources = []
    total_chars = 0

    for hit in hits:
        chunk = f"[{hit['source']}]\n{hit['content']}"
        if total_chars + len(chunk) > CONTEXT_MAX_CHARS:
            break
        context_parts.append(chunk)
        sources.append(hit["source"])
        total_chars += len(chunk)

    context = "\n\n---\n\n".join(context_parts)
    return context, list(set(sources))


def query_incident_history(service_name: str, limit: int = 10) -> list[dict]:
    """Query incidents for a service."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, service, severity, title, started_at, resolved_at, summary "
            "FROM incidents WHERE service = ? ORDER BY started_at DESC LIMIT ?",
            (service_name, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def query_service_dependencies(service_name: str) -> list[dict]:
    """Query service dependency graph."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT upstream, downstream, dependency_type "
            "FROM service_dependencies WHERE upstream = ? OR downstream = ?",
            (service_name, service_name),
        ).fetchall()
    return [dict(r) for r in rows]


def log_agent_interaction(session_id: str, agent: str, query: str, answer: str):
    """Log agent interaction for audit."""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO agent_interactions (session_id, agent, query, answer) VALUES (?, ?, ?, ?)",
            (session_id, agent, query, answer),
        )


def get_conversation_history(session_id: str) -> list[dict]:
    """Retrieve conversation history from Redis."""
    raw = redis_client.get(f"session:{session_id}:history")
    if not raw:
        return []
    return json.loads(raw)


def append_to_history(session_id: str, role: str, content: str):
    """Append a message to conversation history."""
    history = get_conversation_history(session_id)
    history.append({"role": role, "content": content})
    history = history[-20:]
    redis_client.setex(
        f"session:{session_id}:history",
        HISTORY_TTL,
        json.dumps(history),
    )


def execute_code(code: str) -> dict[str, Any]:
    """Execute Python code in a restricted sandbox."""
    import builtins as _builtins_mod

    restricted_globals = {
        "__builtins__": {
            k: getattr(_builtins_mod, k)
            for k in ALLOWED_BUILTINS
            if hasattr(_builtins_mod, k)
        },
    }
    output_lines: list[str] = []

    def _capture_print(*args, **kwargs):
        output_lines.append(" ".join(str(a) for a in args))

    restricted_globals["__builtins__"]["print"] = _capture_print

    try:
        local_vars: dict = {}
        exec(compile(code, "<sandbox>", "exec"), restricted_globals, local_vars)
        return {
            "status": "ok",
            "output": "\n".join(output_lines),
            "locals": {k: repr(v) for k, v in local_vars.items()},
        }
    except Exception:
        return {"status": "error", "output": traceback.format_exc(limit=5)}


# ────────────────────────────────────────────────────────────────────────────
# 6. Agent Functions
# ────────────────────────────────────────────────────────────────────────────

SRE_SYSTEM_PROMPT = """
You are an expert SRE (Site Reliability Engineer) AI assistant.
Your responsibilities:
- Analyze incidents and alerts based on historical data and runbooks.
- Suggest root cause analysis (RCA) and remediation steps.
- Help with on-call triage, runbook lookup, and postmortem drafting.
- Answer questions about service dependencies and SLOs/SLIs.
- Execute diagnostic code snippets safely when needed.

Always:
- Ground your answers in retrieved context from the knowledge base.
- Cite sources when referencing runbooks or past incidents.
- Be concise, structured (use numbered steps for procedures).
- Never reveal secrets, connection strings, or internal credentials.
"""

ENGINEERING_SYSTEM_PROMPT = """
You are an expert Software Engineering AI assistant.
Your responsibilities:
- Answer architecture, design, and code-related questions.
- Review code snippets and suggest improvements.
- Explain service dependencies and integration patterns.
- Help with debugging, performance analysis, and best practices.
- Execute safe code snippets in a sandboxed environment.

Always:
- Ground answers in retrieved internal documentation.
- Cite sources (ADRs, RFCs, wiki pages) when relevant.
- Be precise and use concrete examples.
- Prefer idiomatic, production-ready code suggestions.
- Never output secrets, credentials, or connection strings.
"""


def run_sre_agent(session_id: str, user_message: str) -> dict[str, Any]:
    """Run the SRE agent."""
    logger.info("SRE agent: session=%s query='%s'", session_id, user_message[:80])

    history = []
    try:
        history = get_conversation_history(f"sre:{session_id}")
    except Exception as exc:
        logger.warning("History fetch failed: %s", exc)

    context, sources = retrieve_context(user_message, top_k=settings.rag_top_k)

    incidents = []
    tool_calls = []
    words = user_message.lower().split()
    for word in words:
        if len(word) > 4 and word not in _STOP_WORDS:
            rows = query_incident_history(word)
            if rows:
                incidents = rows
                tool_calls.append(f"query_incident_history(service={word})")
                break

    sandbox_result = None
    if "```python" in user_message:
        start = user_message.find("```python") + 9
        end = user_message.find("```", start)
        if end > start:
            code_block = user_message[start:end].strip()
            sandbox_result = execute_code(code_block)
            tool_calls.append("execute_code(sandbox)")

    messages = [SystemMessage(content=SRE_SYSTEM_PROMPT)]

    if context:
        messages.append(SystemMessage(content=f"Relevant knowledge base context:\n\n{context}"))

    if incidents:
        incident_text = "\n".join(
            f"- [{r['severity']}] {r['title']} at {r['started_at']}: {r.get('summary', '')}"
            for r in incidents
        )
        messages.append(SystemMessage(content=f"Recent incidents:\n{incident_text}"))

    if sandbox_result:
        messages.append(
            SystemMessage(
                content=f"Sandbox execution result:\nStatus: {sandbox_result['status']}\nOutput:\n{sandbox_result['output']}",
            )
        )

    for msg in history[-10:]:
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        else:
            messages.append(AIMessage(content=msg["content"]))

    messages.append(HumanMessage(content=user_message))

    try:
        response = llm.invoke(messages)
        answer = response.content
    except Exception as exc:
        answer = f"[LLM call failed: {exc}]\n\nContext retrieved:\n{context[:500] if context else 'None'}"

    try:
        append_to_history(f"sre:{session_id}", "user", user_message)
        append_to_history(f"sre:{session_id}", "assistant", answer)
    except Exception as exc:
        logger.warning("History save failed: %s", exc)

    try:
        log_agent_interaction(session_id, "sre", user_message, answer)
    except Exception as exc:
        logger.warning("Interaction log failed: %s", exc)

    return {"answer": answer, "sources": sources, "tool_calls": tool_calls}


def run_engineering_agent(session_id: str, user_message: str) -> dict[str, Any]:
    """Run the Engineering agent."""
    logger.info("Engineering agent: session=%s query='%s'", session_id, user_message[:80])

    history = []
    try:
        history = get_conversation_history(f"eng:{session_id}")
    except Exception as exc:
        logger.warning("History fetch failed: %s", exc)

    context, sources = retrieve_context(user_message, top_k=settings.rag_top_k)

    dependencies = []
    tool_calls = []
    words = user_message.lower().split()
    for word in words:
        if len(word) > 4 and word not in _STOP_WORDS:
            rows = query_service_dependencies(word)
            if rows:
                dependencies = rows
                tool_calls.append(f"query_service_dependencies(service={word})")
                break

    sandbox_result = None
    if "```python" in user_message:
        start = user_message.find("```python") + 9
        end = user_message.find("```", start)
        if end > start:
            code_block = user_message[start:end].strip()
            sandbox_result = execute_code(code_block)
            tool_calls.append("execute_code(sandbox)")

    messages = [SystemMessage(content=ENGINEERING_SYSTEM_PROMPT)]

    if context:
        messages.append(SystemMessage(content=f"Relevant internal documentation:\n\n{context}"))

    if dependencies:
        dep_text = "\n".join(
            f"- {r['upstream']} → {r['downstream']} ({r['dependency_type']})"
            for r in dependencies
        )
        messages.append(SystemMessage(content=f"Service dependencies:\n{dep_text}"))

    if sandbox_result:
        messages.append(
            SystemMessage(
                content=f"Sandbox execution result:\nStatus: {sandbox_result['status']}\nOutput:\n{sandbox_result['output']}",
            )
        )

    for msg in history[-10:]:
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        else:
            messages.append(AIMessage(content=msg["content"]))

    messages.append(HumanMessage(content=user_message))

    try:
        response = llm.invoke(messages)
        answer = response.content
    except Exception as exc:
        answer = f"[LLM call failed: {exc}]\n\nContext retrieved:\n{context[:500] if context else 'None'}"

    try:
        append_to_history(f"eng:{session_id}", "user", user_message)
        append_to_history(f"eng:{session_id}", "assistant", answer)
    except Exception as exc:
        logger.warning("History save failed: %s", exc)

    try:
        log_agent_interaction(session_id, "engineering", user_message, answer)
    except Exception as exc:
        logger.warning("Interaction log failed: %s", exc)

    return {"answer": answer, "sources": sources, "tool_calls": tool_calls}


def check_health() -> dict[str, Any]:
    """Check health of all services."""
    services = {}
    overall = "ok"

    try:
        redis_client.ping()
        services["redis"] = "✅ ok (local in-memory)"
    except Exception as exc:
        services["redis"] = f"❌ error: {exc}"
        overall = "degraded"

    try:
        with get_db() as conn:
            conn.execute("SELECT 1").fetchone()
        services["postgres"] = "✅ ok (local SQLite)"
    except Exception as exc:
        services["postgres"] = f"❌ error: {exc}"
        overall = "degraded"

    try:
        llm.invoke([HumanMessage(content="ping")])
        services["llm"] = f"✅ ok (provider={settings.llm_provider}, model={settings.openai_model})"
    except Exception as exc:
        services["llm"] = f"❌ degraded: {exc}"
        overall = "degraded"

    try:
        count = kb.count
        services["chromadb"] = f"✅ ok ({count} chunks indexed)"
    except Exception as exc:
        services["chromadb"] = f"❌ error: {exc}"
        overall = "degraded"

    return {"status": overall, "services": services}


# ────────────────────────────────────────────────────────────────────────────
# 7. Streamlit UI
# ────────────────────────────────────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="Agentic RAG",
        page_icon="🤖",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.title("🤖 Agentic RAG Platform")
    st.markdown("Local SRE + Engineering agents backed by ChromaDB and SQLite")

    # Initialize session state
    if "session_id" not in st.session_state:
        st.session_state.session_id = f"sess-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = []
    if "selected_agent" not in st.session_state:
        st.session_state.selected_agent = "sre"

    # Sidebar
    with st.sidebar:
        st.header("⚙️ Configuration")
        st.session_state.selected_agent = st.radio(
            "Select Agent",
            options=["sre", "engineering"],
            format_func=lambda x: "🚨 SRE" if x == "sre" else "🏗️ Engineering",
        )
        st.session_state.session_id = st.text_input(
            "Session ID",
            value=st.session_state.session_id,
            help="Unique identifier for this conversation",
        )
        st.markdown("---")
        st.caption(f"📂 Project: {PROJECT_ROOT.name}")
        st.caption(f"💾 DB: {DB_PATH.name}")
        st.caption(f"📚 Docs: {len(list((PROJECT_ROOT / settings.docs_dir).glob('*')))} files")

    # Create tabs
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "💬 Chat",
        "📄 Ingest",
        "🩺 Health",
        "📋 Audit Log",
        "🗂️ History",
    ])

    # ─────────────────────────────────────────────────────────────────────────
    # TAB 1: CHAT
    # ─────────────────────────────────────────────────────────────────────────
    with tab1:
        st.header("💬 Chat")
        st.markdown(f"**Agent**: {st.session_state.selected_agent.upper()} | **Session**: `{st.session_state.session_id}`")

        # Display chat history
        messages_container = st.container(height=400, border=True)
        with messages_container:
            history = get_conversation_history(f"{st.session_state.selected_agent[0:3]}:{st.session_state.session_id}")
            for msg in history:
                if msg["role"] == "user":
                    with st.chat_message("user"):
                        st.write(msg["content"])
                else:
                    with st.chat_message("assistant"):
                        st.write(msg["content"])

        # Chat input and submission
        col1, col2 = st.columns([0.85, 0.15])
        with col1:
            user_input = st.text_area(
                "Your message",
                placeholder="Ask me about incidents, architecture, code reviews, or anything else...",
                height=80,
                label_visibility="collapsed",
            )
        with col2:
            submit_button = st.button("🚀 Send", use_container_width=True)

        if submit_button and user_input.strip():
            with st.spinner(f"🔄 {st.session_state.selected_agent.upper()} agent is thinking..."):
                if st.session_state.selected_agent == "sre":
                    result = run_sre_agent(st.session_state.session_id, user_input)
                else:
                    result = run_engineering_agent(st.session_state.session_id, user_input)

            # Display response
            st.divider()
            with st.chat_message("assistant"):
                st.write(result["answer"])

            # Display metadata
            if result["sources"]:
                with st.expander("📚 Sources"):
                    for src in result["sources"]:
                        st.caption(f"• {src}")

            if result["tool_calls"]:
                with st.expander("🔧 Tools Used"):
                    for call in result["tool_calls"]:
                        st.code(call, language="python")

            st.success("✅ Message saved to history")
            st.rerun()

        # Clear history button
        if st.button("🗑️ Clear Chat History", key="clear_chat"):
            redis_client._store.clear()
            st.success("Chat history cleared")
            st.rerun()

    # ─────────────────────────────────────────────────────────────────────────
    # TAB 2: INGEST
    # ─────────────────────────────────────────────────────────────────────────
    with tab2:
        st.header("📄 Ingest Documents")
        st.markdown("Upload `.md` or `.txt` files to add them to the knowledge base.")

        uploaded_files = st.file_uploader(
            "Choose files",
            type=["md", "txt"],
            accept_multiple_files=True,
        )

        col1, col2 = st.columns(2)
        with col1:
            clear_existing = st.checkbox(
                "Clear existing documents first",
                help="Delete all previously indexed documents",
            )
        with col2:
            ingest_button = st.button("📤 Ingest Files", use_container_width=True)

        if ingest_button:
            if uploaded_files:
                with st.spinner("Ingesting files..."):
                    # Save uploaded files
                    docs_dir = PROJECT_ROOT / settings.docs_dir
                    for uploaded_file in uploaded_files:
                        file_path = docs_dir / uploaded_file.name
                        file_path.write_bytes(uploaded_file.getbuffer())

                    # Reingest
                    result = kb.ingest_directory(docs_dir, clear_existing=clear_existing)

                    st.success(
                        f"✅ Ingestion complete!\n\n"
                        f"- Files indexed: {result['files_indexed']}\n"
                        f"- Chunks created: {result['chunks_indexed']}\n"
                        f"- Total chunks: {result['collection_count']}"
                    )
            else:
                st.warning("Please upload at least one file")

        # Show current documents
        st.subheader("Current Knowledge Base")
        docs_dir = PROJECT_ROOT / settings.docs_dir
        doc_files = list(docs_dir.glob("*.md")) + list(docs_dir.glob("*.txt"))
        if doc_files:
            for doc_file in doc_files:
                size_kb = doc_file.stat().st_size / 1024
                st.caption(f"📄 {doc_file.name} ({size_kb:.1f} KB)")
        else:
            st.info("No documents yet. Upload some `.md` or `.txt` files!")

    # ─────────────────────────────────────────────────────────────────────────
    # TAB 3: HEALTH
    # ─────────────────────────────────────────────────────────────────────────
    with tab3:
        st.header("🩺 Health Check")

        if st.button("🔄 Refresh", key="refresh_health"):
            pass

        health = check_health()

        # Overall status
        status_color = "🟢" if health["status"] == "ok" else "🟡"
        st.metric(
            "Overall Status",
            health["status"].upper(),
            delta=f"{status_color}",
        )

        # Service statuses
        st.subheader("Service Details")
        cols = st.columns(2)
        for idx, (service, status) in enumerate(health["services"].items()):
            with cols[idx % 2]:
                st.write(f"**{service}**")
                st.code(status, language="text")

    # ─────────────────────────────────────────────────────────────────────────
    # TAB 4: AUDIT LOG
    # ─────────────────────────────────────────────────────────────────────────
    with tab4:
        st.header("📋 Audit Log")

        # Filters
        col1, col2 = st.columns(2)
        with col1:
            filter_agent = st.selectbox(
                "Filter by Agent",
                options=["all", "sre", "engineering"],
            )
        with col2:
            filter_limit = st.slider(
                "Show last N interactions",
                min_value=5,
                max_value=100,
                value=20,
            )

        # Fetch audit log
        with get_db() as conn:
            if filter_agent == "all":
                rows = conn.execute(
                    "SELECT session_id, agent, query, answer, created_at FROM agent_interactions "
                    "ORDER BY created_at DESC LIMIT ?",
                    (filter_limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT session_id, agent, query, answer, created_at FROM agent_interactions "
                    "WHERE agent = ? ORDER BY created_at DESC LIMIT ?",
                    (filter_agent, filter_limit),
                ).fetchall()

        if rows:
            for row in rows:
                with st.expander(
                    f"[{row['agent'].upper()}] {row['created_at']} — {row['query'][:60]}..."
                ):
                    st.markdown(f"**Session**: `{row['session_id']}`")
                    st.markdown(f"**Agent**: {row['agent']}")
                    st.markdown(f"**Query**: {row['query']}")
                    st.markdown(f"**Answer**: {row['answer']}")
        else:
            st.info("No interactions yet")

    # ─────────────────────────────────────────────────────────────────────────
    # TAB 5: HISTORY
    # ─────────────────────────────────────────────────────────────────────────
    with tab5:
        st.header("🗂️ Conversation History")

        # Get unique session IDs
        with get_db() as conn:
            session_ids = conn.execute(
                "SELECT DISTINCT session_id FROM agent_interactions ORDER BY session_id DESC"
            ).fetchall()

        session_options = [row["session_id"] for row in session_ids]

        if session_options:
            selected_session = st.selectbox(
                "Select a session",
                options=session_options,
            )

            with get_db() as conn:
                interactions = conn.execute(
                    "SELECT agent, query, answer, created_at FROM agent_interactions "
                    "WHERE session_id = ? ORDER BY created_at ASC",
                    (selected_session,),
                ).fetchall()

            if interactions:
                st.subheader(f"Session: `{selected_session}`")
                st.markdown(f"**Total interactions**: {len(interactions)}")

                for interaction in interactions:
                    with st.expander(
                        f"[{interaction['agent'].upper()}] {interaction['created_at']}"
                    ):
                        st.markdown(f"**Q**: {interaction['query']}")
                        st.markdown(f"**A**: {interaction['answer']}")
        else:
            st.info("No conversation history yet")


if __name__ == "__main__":
    main()