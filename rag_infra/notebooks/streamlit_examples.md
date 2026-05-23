# Streamlit App — Example Queries

Three hands-on examples to exercise the SRE and Engineering agents in the Streamlit chat UI.

---

## How to Launch

```bash
cd c:\ML\AgenticAI\rag-infra\notebooks
streamlit run streamlit_app.py
```

---

## Example 1: SRE Agent — Incident Triage with Runbook Lookup

**Agent:** `sre`

### Step 1 — Ask about a live incident

Paste this into the chat input:

> We're seeing 5xx errors on payment-service during peak hours. The error rate jumped from 0.1% to 12% in the last 15 minutes. What's the likely root cause and what should I check first?

**What happens:**
- The SRE agent queries the `incidents` table for `payment-service` → triggers `query_incident_history(service=payment-service)`
- Retrieves the payment-service runbook from the search index (connection pool checks, Stripe API key validation)
- Combines incident history + runbook context into a grounded response with cited sources

### Step 2 — Follow-up in the same session

Keep the same Session ID and ask:

> The logs show PoolExhausted exceptions. How do I increase the connection pool and what's the safe limit?

**What happens:**
- Conversation history is preserved in the local Redis store, so the agent knows you're still discussing payment-service 5xx errors
- Gives a contextual follow-up referencing the runbook's connection pool section

**Features exercised:** RAG retrieval, incident history lookup, multi-turn conversation history

---

## Example 2: Engineering Agent — Code Review with Sandbox Execution

**Agent:** `engineering`

### Step 1 — Submit a code review with an executable snippet

Paste this into the chat input:

````
Review this retry logic for our order-service client and run it to verify the backoff timing:

```python
delays = []
for attempt in range(5):
    delay = min(2 ** attempt * 0.5, 10)
    delays.append(delay)
    print(f"Attempt {attempt+1}: wait {delay:.1f}s before retry")
print(f"Total max wait: {sum(delays):.1f}s")
```
````

**What happens:**
- The Engineering agent retrieves relevant architecture docs from the search index
- Detects the Python code block and executes it in the sandbox → triggers `execute_code(sandbox)`
- Includes the sandbox output (backoff timing) in the response
- Reviews the retry pattern and suggests improvements (e.g., exponential backoff with jitter, circuit breaker)

### Step 2 — Ask about dependencies

In the same session, follow up with:

> What services depend on order-service and what happens if it goes down?

**What happens:**
- Queries the `service_dependencies` table → triggers `query_service_dependencies(service=order-service)`
- Returns the dependency graph: `api-gateway → order-service`, `order-service → payment-service`, `order-service → inventory-service`, `notification-service → order-service`
- Explains the blast radius of an order-service outage

**Features exercised:** Sandbox code execution, service dependency lookup, RAG retrieval, multi-turn history

---

## Example 3: Ingest a Custom Document, Then Query It

### Step 1 — Create a runbook file

Create a file called `auth-runbook.md` with this content:

```markdown
# Auth Service Runbook

## OAuth Token Endpoint Failures
1. Check TLS certificate expiry: `openssl s_client -connect auth:443`
2. Verify Azure AD app registration hasn't expired
3. Check Redis session cache connectivity
4. Review rate limits — auth-service allows 500 req/s per client

## Rotating Secrets
1. Generate new client secret in Azure AD portal
2. Update Key Vault secret `auth-client-secret`
3. Restart auth-service pods: `kubectl rollout restart deploy/auth-service`
```

### Step 2 — Ingest the document

1. Go to the **📄 Ingest Documents** tab
2. Upload `auth-runbook.md`
3. Click **Ingest**
4. Verify the success message shows the new chunk count

### Step 3 — Query the SRE agent

Switch to the **💬 Chat** tab, select **Agent: sre**, and ask:

> The auth-service OAuth token endpoint is returning 401s for all clients. Walk me through the troubleshooting steps.

**What happens:**
- The newly ingested runbook is now in the search index
- The SRE agent retrieves the relevant auth-runbook chunks via keyword search
- Also finds the seeded `auth-service` incident from SQLite ("OAuth token endpoint down — expired TLS cert")
- Combines both sources into a step-by-step troubleshooting response

### Step 4 — Verify on the Health tab

Go to the **🩺 Health** tab and confirm that the Search Index shows an increased chunk count.

**Features exercised:** Document ingestion, RAG retrieval, incident history lookup, health dashboard

---

## Summary of Features Covered

| Feature | Example 1 | Example 2 | Example 3 |
|---|:---:|:---:|:---:|
| RAG retrieval | ✅ | ✅ | ✅ |
| Incident history lookup | ✅ | | ✅ |
| Service dependency query | | ✅ | |
| Sandbox code execution | | ✅ | |
| Multi-turn conversation | ✅ | ✅ | |
| Document ingestion | | | ✅ |
| Health dashboard | | | ✅ |
