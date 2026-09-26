---
doc: ARCHITECTURE
status: draft
last_verified_commit: none (pre-repo)
updated: 2026-09-25
---

# Architecture

## Overview

```
client/agent ──► Gateway (FastAPI)
                   │
                   ├─ Auth + opt-out headers
                   ├─ Semantic Cache lookup ──hit──► replay response (JSON or SSE)
                   │
                   ├─ miss ─► Provider Adapter ─► upstream (Anthropic)
                   │               │
                   │               └─ Stream Tee ─► client (live)
                   │                            └─► usage parser
                   │
                   └─ async post-processing (never blocks the client):
                        Segmenter → Hasher → Lineage → Diff → Classifier → Store
                        Eligibility → Embedder → VectorStore + ResponseStore
```

Plain version: requests come in, the gateway checks its "already answered" shelf, otherwise passes the request through and streams the answer straight back. After the client has its answer, the gateway does its analysis in the background.

## Components

### 1. Gateway (FastAPI)
- Routes: provider-native ingress per wire format (`POST /v1/messages`, `/v1/chat/completions`, `/v1/responses`, Gemini `generateContent`), optionally prefixed `/u/{upstream}/`; admin routes under `/pcg/*`.
- Reads headers: `x-pcg-upstream`, `x-pcg-session`, `x-pcg-cache: bypass`, `x-pcg-log: off|hash|full`, `x-pcg-tags`.
- Fail-open wrapper around every internal component (REQ-GW-06).

### 2. Provider Adapters (ADR-011, 012, 013)
One adapter per wire format: `anthropic-messages`, `openai-chat`, `openai-responses`, `gemini`. Each converts wire requests into a `CanonicalRequest` (ordered tools, system/developer blocks, messages, params, stream flag); core components only see canonical types.

Contract:
- `wire_format`, `ingress_routes()`, `auth_header_names()` (x-api-key / Authorization / x-goog-api-key)
- `parse_request(bytes) -> CanonicalRequest + segments` (keeps raw serialized bytes per segment for the raw hash)
- `forward(request, upstream) -> Response | AsyncIterator[SSEEvent]`, `extract_usage(...) -> Usage`
- `cache_profile() -> CacheProfile`
- `is_cacheable_response(...)`: maps tool calls (`tool_use` / `tool_calls` / `function_call`) and finish reasons to canonical `complete`
- `render_response(entry)`, `synthesize_sse(entry)`: replay in the client's own wire format

`CacheProfile`: `cache_mode` (EXPLICIT_BREAKPOINT | AUTO_PREFIX | NONE), `min_cacheable_tokens(model)`, `default_ttl_s`, `usage_reporting` (full | read_only | none), `segment_order`.

`Usage`: input, output, `cache_write | None`, `cache_read | None` (None = provider does not report; not 0).

Rollout: anthropic-messages (Phase 1), openai-chat (Phase 2; covers OpenAI, Azure OpenAI, Groq, Together, Mistral, DeepSeek, xAI, OpenRouter, Fireworks, vLLM, Ollama, LM Studio, Gemini OpenAI-compat), openai-responses and gemini (Phase 5). Bedrock/Vertex deferred (request signing).

A shared adapter conformance suite must pass for every adapter.

Uses a shared `httpx.AsyncClient` with connection pooling.

#### Upstream registry
`PCG_UPSTREAMS` maps name to `{wire_format, base_url, auth_style}`, validated against an allowlist at startup. Clients pick one via `/u/{name}/...` or `x-pcg-upstream`. Bare `/v1/messages` and `/v1/chat/completions` use `PCG_DEFAULT_UPSTREAM`. No failover or load balancing.

### 3. Stream Tee
- Yields upstream SSE chunks to the client immediately while copying them to an internal buffer.
- Parses `message_start` / `message_delta` events for usage and stop_reason.

### 4. Segmenter + Hasher
- Emits ordered segments: each tool definition, each system block, each message (with role and index).
- Records any `cache_control` breakpoint positions.
- Two hashes per segment (ADR-006):
  - `raw_hash`: SHA-256 of the exact serialized bytes as sent.
  - `canon_hash`: SHA-256 of canonical JSON (sorted keys, normalized whitespace).
- Plain version: one fingerprint for "exactly what was sent", one for "what it means". If the first changed but the second didn't, the cache broke on formatting alone.

### 5. Lineage Tracker
- If `x-pcg-session` present: predecessor = latest request in that session.
- Else: among recent requests (window, OQ-3) with same tenant + model, pick the one with the longest common `raw_hash` prefix.

### 6. Diff Engine
- Finds first index where `raw_hash` differs.
- Produces a unified text diff of that segment (bounded length) plus segment metadata.

### 7. Classifier
Inputs: divergence index, breakpoint positions, time since predecessor, usage numbers, model, and the adapter's `CacheProfile`. Rules are selected by `cache_mode`: EXPLICIT_BREAKPOINT uses breakpoints as below; AUTO_PREFIX has no breakpoints, so HEALTHY/BUST is judged from the first divergence versus `min_cacheable_tokens` and the provider's cache granularity; NONE or `usage_reporting = none` yields NOT_REPORTED.

| Class | Rule (simplified) |
|---|---|
| NOT_REPORTED | Provider reports no cache usage (`usage_reporting = none`). Hash diff and lineage still produced. |
| COLD_START | No predecessor. |
| HEALTHY | cache_read_tokens > 0 and roughly equals prefix up to last breakpoint before divergence. |
| TTL_EXPIRED | No divergence before breakpoint, but gap > configured TTL, cache_read = 0. |
| BELOW_MIN_LENGTH | Prefix before breakpoint under model's minimum cacheable length. |
| BUST | Divergence before a breakpoint within TTL, cache_read = 0 or reduced. |
| UNEXPLAINED | None of the above match. |

Diagnosis rules for BUST (REQ-BD-07) run against the diff: regexes for ISO timestamps / UUIDs, tool name order comparison, raw≠canon equality check, earlier-message mutation detection, model field change, breakpoint placed after a segment that changed in the last N requests.

### 8. Semantic Cache
- **Fingerprint**: `sha256(tenant_hash, wire_format, upstream, model, canon_hash(system), canon_hash(tools), policy_version)` (ADR-005, 012). Hits never cross wire formats; stored responses are wire-native.
- **Embedder**: interface `embed(text) -> list[float]`; default local model (OQ-2).
- **VectorStore** interface (ADR-004):
  - `upsert(scope, id, vector, meta)`
  - `query(scope, vector, top_k, filter) -> [(id, score, meta)]`
  - `delete(scope, ids | filter)`
  - `purge_expired(now)`
  - `scope` maps to a Qdrant payload filter / Pinecone namespace / Upstash namespace.
- **ResponseStore**: full response bodies keyed by entry id (relational DB). Keeps large payloads out of vector metadata limits.
- **Eligibility policy** (REQ-SC-04) evaluated before write.
- **Replay**: on hit, the adapter rebuilds a response in the client's wire format; if streaming was requested, it synthesizes that dialect's SSE (Anthropic events, OpenAI `data:` chunks + `[DONE]`, Gemini stream chunks).

### 9. Store (relational)
- SQLAlchemy 2.x + Alembic. SQLite in dev, Postgres when deployed (ADR-003).

### 10. CLI (Phase 3) and Dashboard (Phase 5)
- CLI: `pcg report --session X`, `pcg busts --since 1h`.
- Dashboard: Next.js reading the admin API.

## Data model

```
requests(
  id uuid pk, created_at, tenant_hash, session_id null, model,
  wire_format, upstream, cache_mode,
  predecessor_id null fk requests, divergence_index null,
  classification, diagnosis null,
  input_tokens, cache_write_tokens null, cache_read_tokens null, output_tokens,
  latency_ms, upstream_status, semantic_result (hit|miss|bypass|ineligible),
  semantic_score null, log_mode, body_ref null
)
segments(
  request_id fk, idx, kind (tool|system|message), role null,
  raw_hash, canon_hash, byte_len, has_breakpoint bool,
  pk(request_id, idx)
)
bust_events(
  id pk, request_id fk, segment_idx, diagnosis, diff_excerpt text,
  est_tokens_lost
)
cache_entries(
  id uuid pk, fingerprint, tenant_hash, wire_format, created_at, expires_at,
  tags text[], similarity_threshold_used, response_body jsonb,
  source_request_id fk requests, hit_count int
)
bodies(
  id pk, request_id fk, prompt jsonb null, response jsonb null, expires_at
)
```

Access control: single-tenant deployment in v1. No Supabase/RLS (ADR-007). Admin API protected by gateway token; every query filters by tenant_hash where applicable.

## Environment

| Var | Purpose |
|---|---|
| `PCG_UPSTREAMS` | Named upstreams: `{name: {wire_format, base_url, auth_style}}`, allowlisted at startup. |
| `PCG_DEFAULT_UPSTREAM` | Upstream used by bare `/v1/messages` and `/v1/chat/completions`. |
| `PCG_ADMIN_TOKEN` | Admin/dashboard auth. |
| `PCG_DB_URL` | SQLite path or Postgres URL. |
| `PCG_VECTOR_BACKEND` | `qdrant` \| `pinecone` \| `upstash` \| `memory`. |
| `PCG_VECTOR_URL`, `PCG_VECTOR_API_KEY` | Vector backend connection. |
| `PCG_EMBED_MODEL` | Embedding model id. |
| `PCG_SIM_THRESHOLD` | Default similarity threshold. |
| `PCG_CACHE_TTL_S` | Semantic cache entry TTL. |
| `PCG_PROVIDER_CACHE_TTL_S` | Assumed provider cache TTL for classification (default 300). |
| `PCG_LOG_MODE` | `off` \| `hash` \| `full`. |
| `PCG_BODY_RETENTION_H` | Body retention window. |

## Deployment
- Dev: `uvicorn` locally, SQLite, Qdrant local mode (in-process, no Docker) or `memory` backend.
- Test/CI: GitHub Actions (ADR-014). Same as dev; recorded upstream fixtures, in-process/`memory` vector backend, no cloud credentials. Live provider and Qdrant Cloud tests only in the manual `integration.yml` using repo secrets.
- Release: version tag triggers `release.yml` to build, scan and publish the Docker image (Phase 6).
- Hosted vector store: Qdrant Cloud cluster (ADR-010). Credentials via `PCG_VECTOR_URL` / `PCG_VECTOR_API_KEY` from a gitignored `.env`; never committed, never logged.
- Later: Docker image; Postgres.

## Source-of-truth rules
- Provider-reported usage is the truth for whether caching happened; the gateway's own prefix math is only used to explain it.
- `requests` table is the source of truth for history; vector store is a disposable index and can be rebuilt from `cache_entries`.
- This doc wins over code comments; DECISIONS.md wins over this doc when they conflict, until this doc is updated.
- External API facts live in API.md with a last-verified date; re-verify before relying on them.
