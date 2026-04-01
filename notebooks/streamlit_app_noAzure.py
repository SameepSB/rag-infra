"""
Streamlit Chat UI for Agentic RAG Platform (No Azure)
=====================================================
Replaces the FastAPI routes with a simple browser-based interface.
Uses standard OpenAI via LangChain ChatOpenAI + ChromaDB for RAG — no Azure dependency.

Run with:
    streamlit run streamlit_app_noAzure.py
"""
import streamlit as st
import sys, os, json, hashlib, logging, sqlite3, traceback, uuid
from pathlib import Path
from typing import Any, Optional
from enum import Enum

# ── Ensure project root is importable ─────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pydantic_settings import BaseSettings, SettingsConfigDict
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
import chromadb
from chromadb.utils import embedding_functions
from langchain_text_splitters import RecursiveCharacterTextSplitter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("streamlit_app")

# ══════════════════════════════════════════════════════════════════════════════
# Settings
# ══════════════════════════════════════════════════════════════════════════════
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore")
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    llm_temperature: float = 0.2
    data_dir: Path = Path("data")
    db_path: Path = Path("data/demo_local.db")
    docs_dir: Path = Path("data/raw-docs")
    chroma_rag_dir: Path = Path("data/chroma_rag")
    rag_chunk_size: int = 800
    rag_chunk_overlap: int = 120
    rag_top_k: int = 5

settings = Settings()

# ══════════════════════════════════════════════════════════════════════════════
# LangChain ChatOpenAI LLM (required)
# ══════════════════════════════════════════════════════════════════════════════
if not settings.openai_api_key:
    st.error(
        "❌ OPENAI_API_KEY must be set in .env file. "
        "Please add it and restart the app."
    )
    st.stop()

llm = ChatOpenAI(
    model=settings.openai_model,
    api_key=settings.openai_api_key,
    temperature=settings.llm_temperature,
)

# ══════════════════════════════════════════════════════════════════════════════
# Local clients
# ══════════════════════════════════════════════════════════════════════════════
class LocalRedis:
    def __init__(self):
        self._store: dict[str, str] = {}
    def get(self, key: str) -> Optional[str]:
        return self._store.get(key)
    def setex(self, key: str, ttl: int, value: str):
        self._store[key] = value
    def ping(self) -> bool:
        return True

redis_client = LocalRedis()

DB_PATH = PROJECT_ROOT / settings.db_path
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

LOCAL_DOCS_DIR = PROJECT_ROOT / settings.docs_dir
LOCAL_DOCS_DIR.mkdir(parents=True, exist_ok=True)

# ══════════════════════════════════════════════════════════════════════════════
# ChromaDB Knowledge Base
# ══════════════════════════════════════════════════════════════════════════════
class KnowledgeBaseService:
    def __init__(self, settings):
        chroma_path = PROJECT_ROOT / settings.chroma_rag_dir
        chroma_path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(chroma_path))
        self._collection_name = "rag_knowledge_base"
        self._ef = embedding_functions.DefaultEmbeddingFunction()
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name, embedding_function=self._ef,
        )
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.rag_chunk_size, chunk_overlap=settings.rag_chunk_overlap,
        )
        self._settings = settings

    def ingest_directory(self, directory, clear_existing=False):
        if clear_existing:
            self._client.delete_collection(name=self._collection_name)
            self._collection = self._client.get_or_create_collection(
                name=self._collection_name, embedding_function=self._ef,
            )
        files = sorted([*directory.glob("*.md"), *directory.glob("*.txt")])
        docs, ids, metas = [], [], []
        for fp in files:
            text = fp.read_text(encoding="utf-8", errors="ignore")
            for i, chunk in enumerate(self._splitter.split_text(text)):
                h = hashlib.sha1(chunk.encode()).hexdigest()[:10]
                docs.append(chunk); ids.append(f"{fp.stem}-{i}-{h}")
                metas.append({"source": fp.name, "chunk_index": i})
        if docs:
            self._collection.upsert(documents=docs, ids=ids, metadatas=metas)
        return {"files_indexed": len(files), "chunks_indexed": len(docs), "collection_count": self._collection.count()}

    def search(self, query, top_k=None):
        if self._collection.count() == 0:
            return []
        r = self._collection.query(query_texts=[query], n_results=top_k or self._settings.rag_top_k,
                                    include=["documents", "metadatas", "distances"])
        docs = (r.get("documents") or [[]])[0]
        metas = (r.get("metadatas") or [[]])[0]
        dists = (r.get("distances") or [[]])[0]
        return [{"content": docs[i], "source": (metas[i] or {}).get("source", "unknown"),
                 "distance": dists[i] if i < len(dists) else None} for i in range(len(docs))]

    @property
    def count(self):
        return self._collection.count()

kb = KnowledgeBaseService(settings)

def retrieve_context(query, top_k=5):
    hits = kb.search(query, top_k=top_k)
    parts, srcs, total = [], [], 0
    for h in hits:
        chunk = f"[{h['source']}]\n{h['content']}"
        if total + len(chunk) > 6000:
            break
        parts.append(chunk); srcs.append(h["source"]); total += len(chunk)
    return "\n\n---\n\n".join(parts), list(set(srcs))

# ══════════════════════════════════════════════════════════════════════════════
# DB Tools
# ══════════════════════════════════════════════════════════════════════════════
def query_incident_history(service_name, limit=10):
    with _get_db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM incidents WHERE service = ? ORDER BY started_at DESC LIMIT ?",
            (service_name, limit)).fetchall()]

def query_service_dependencies(service_name):
    with _get_db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT upstream, downstream, dependency_type FROM service_dependencies WHERE upstream = ? OR downstream = ?",
            (service_name, service_name)).fetchall()]

def log_agent_interaction(session_id, agent, query, answer):
    with _get_db() as conn:
        conn.execute("INSERT INTO agent_interactions (session_id, agent, query, answer) VALUES (?, ?, ?, ?)",
                     (session_id, agent, query, answer))

def get_conversation_history(session_id):
    raw = redis_client.get(f"session:{session_id}:history")
    return json.loads(raw) if raw else []

def append_to_history(session_id, role, content):
    h = get_conversation_history(session_id)
    h.append({"role": role, "content": content})
    redis_client.setex(f"session:{session_id}:history", 3600, json.dumps(h[-20:]))

import builtins as _bm
_AB = {"print","len","range","enumerate","zip","list","dict","set","tuple","str","int","float","bool","type","isinstance","min","max","sum"}
def execute_code(code):
    rg = {"__builtins__": {k: getattr(_bm, k) for k in _AB if hasattr(_bm, k)}}
    ol = []
    rg["__builtins__"]["print"] = lambda *a, **kw: ol.append(" ".join(str(x) for x in a))
    try:
        lv = {}
        exec(compile(code, "<sandbox>", "exec"), rg, lv)
        return {"status": "ok", "output": "\n".join(ol)}
    except Exception:
        return {"status": "error", "output": traceback.format_exc(limit=5)}

_STOP_WORDS = {"what","when","where","which","there","their","about","would","could","should","have","been","that","this","with","from","your","into","will","more","also"}

# ══════════════════════════════════════════════════════════════════════════════
# Agent runners (using LangChain ChatOpenAI)
# ══════════════════════════════════════════════════════════════════════════════
SRE_PROMPT = "You are an expert SRE AI assistant. Analyze incidents, suggest RCA and remediation. Ground answers in retrieved context. Be concise."
ENG_PROMPT = "You are an expert Software Engineering AI assistant. Answer architecture and code questions. Ground answers in retrieved docs. Be precise."

def _run_agent(session_id, user_message, agent_type):
    prefix = "sre" if agent_type == "sre" else "eng"
    system_prompt = SRE_PROMPT if agent_type == "sre" else ENG_PROMPT
    history = get_conversation_history(f"{prefix}:{session_id}")
    context, sources = retrieve_context(user_message, top_k=settings.rag_top_k)
    tool_calls = []

    db_context = ""
    for word in user_message.lower().split():
        if len(word) > 4 and word not in _STOP_WORDS:
            if agent_type == "sre":
                rows = query_incident_history(word)
                if rows:
                    db_context = "Recent incidents:\n" + "\n".join(f"- [{r['severity']}] {r['title']}: {r.get('summary','')}" for r in rows)
                    tool_calls.append(f"query_incident_history(service={word})")
                    break
            else:
                rows = query_service_dependencies(word)
                if rows:
                    db_context = "Service dependencies:\n" + "\n".join(f"- {r['upstream']} → {r['downstream']} ({r['dependency_type']})" for r in rows)
                    tool_calls.append(f"query_service_dependencies(service={word})")
                    break

    sandbox_result = None
    if "```python" in user_message:
        s = user_message.find("```python") + 9
        e = user_message.find("```", s)
        if e > s:
            sandbox_result = execute_code(user_message[s:e].strip())
            tool_calls.append("execute_code(sandbox)")

    msgs = [SystemMessage(content=system_prompt)]
    if context:
        msgs.append(SystemMessage(content=f"Retrieved context:\n\n{context}"))
    if db_context:
        msgs.append(SystemMessage(content=db_context))
    if sandbox_result:
        msgs.append(SystemMessage(content=f"Sandbox result:\n{sandbox_result['output']}"))
    for msg in history[-10:]:
        if msg["role"] == "user":
            msgs.append(HumanMessage(content=msg["content"]))
        else:
            msgs.append(AIMessage(content=msg["content"]))
    msgs.append(HumanMessage(content=user_message))

    try:
        resp = llm.invoke(msgs)
        answer = resp.content
    except Exception as exc:
        answer = f"[LLM error: {exc}]\n\nContext:\n{context[:500]}" if context else f"[LLM error: {exc}]"

    append_to_history(f"{prefix}:{session_id}", "user", user_message)
    append_to_history(f"{prefix}:{session_id}", "assistant", answer)
    try:
        log_agent_interaction(session_id, agent_type, user_message, answer)
    except Exception:
        pass
    return {"answer": answer, "sources": sources, "tool_calls": tool_calls}

# ══════════════════════════════════════════════════════════════════════════════
# Startup: ingest local docs into ChromaDB
# ══════════════════════════════════════════════════════════════════════════════
kb.ingest_directory(LOCAL_DOCS_DIR)

# ══════════════════════════════════════════════════════════════════════════════
# Streamlit UI
# ══════════════════════════════════════════════════════════════════════════════
st.set_page_config(page_title="Agentic RAG Chat", page_icon="🤖", layout="wide")
st.title("🤖 Agentic RAG Platform — Local Demo")

tab_chat, tab_ingest, tab_health = st.tabs(["💬 Chat", "📄 Ingest Documents", "🩺 Health"])

with tab_chat:
    col1, col2 = st.columns([1, 3])
    with col1:
        agent_type = st.radio("Agent", ["sre", "engineering"], index=0)
        session_id = st.text_input("Session ID", value=str(uuid.uuid4())[:8])

    with col2:
        if "messages" not in st.session_state:
            st.session_state.messages = []

        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        if prompt := st.chat_input("Ask the agent..."):
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    result = _run_agent(session_id, prompt, agent_type)
                st.markdown(result["answer"])
                if result["sources"]:
                    st.caption(f"📚 Sources: {', '.join(result['sources'])}")
                if result["tool_calls"]:
                    st.caption(f"🔧 Tools used: {', '.join(result['tool_calls'])}")

            st.session_state.messages.append({"role": "assistant", "content": result["answer"]})

with tab_ingest:
    st.subheader("Upload documents to the knowledge base")
    uploaded = st.file_uploader("Choose .md or .txt files", type=["md", "txt"], accept_multiple_files=True)
    if uploaded and st.button("Ingest"):
        for f in uploaded:
            dest = LOCAL_DOCS_DIR / f.name
            dest.write_bytes(f.getvalue())
        stats = kb.ingest_directory(LOCAL_DOCS_DIR)
        st.success(f"Ingested {stats['chunks_indexed']} chunks from {stats['files_indexed']} file(s)")

with tab_health:
    st.subheader("Service Health")
    svc = {}
    svc["Redis"] = "✅ ok (local in-memory)"
    try:
        with _get_db() as c:
            c.execute("SELECT 1")
        svc["PostgreSQL"] = "✅ ok (local SQLite)"
    except Exception as e:
        svc["PostgreSQL"] = f"❌ {e}"
    svc["OpenAI"] = f"✅ configured (model={settings.openai_model})"
    svc["ChromaDB"] = f"✅ {kb.count} chunks indexed"
    for name, status in svc.items():
        st.markdown(f"**{name}**: {status}")