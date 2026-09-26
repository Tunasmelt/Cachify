---
doc: DECISIONS
status: append-only
last_verified_commit: none (pre-repo)
updated: 2026-09-25
---

# Decision log (append-only)

Format: ID, date, status (Proposed | Accepted | Superseded by X), context, decision, consequences.

## ADR-001: Python + FastAPI reverse proxy
- Date: 2026-09-25 · Status: Accepted
- Context: Need async streaming passthrough and fast iteration; matches existing stack.
- Decision: FastAPI + httpx AsyncClient; SSE handled manually.
- Consequences: Python overhead on the hot path; keep hot path minimal, push analysis to background tasks.

## ADR-002: Anthropic first
- Date: 2026-09-25 · Status: Accepted
- Context: Explicit `cache_control` breakpoints and separate cache write/read usage fields make bust detection precise.
- Decision: AnthropicAdapter in Phase 1; OpenAI in Phase 5.
- Consequences: Adapter interface must not leak Anthropic-specific shapes into core.

## ADR-003: Split storage: relational log + vector index
- Date: 2026-09-25 · Status: Accepted
- Context: Bust detection needs lineage, joins, aggregation. Semantic cache needs similarity search.
- Decision: Relational DB (SQLite dev, Postgres deploy) for everything durable; vector store holds only vectors + small metadata.
- Consequences: Vector store is rebuildable; response bodies never depend on vector metadata limits.

## ADR-004: Vector store behind an interface, Qdrant as default
- Date: 2026-09-25 · Status: Proposed (awaiting Harris's confirmation)
- Context: Candidates compared on free tiers: Qdrant Cloud (1 GB free cluster, local in-process mode, payload filters), Pinecone Starter (2 GB, but 100 namespaces per index), Upstash Vector (free, 100 namespaces, 10K queries/day, 1536 max dims), Redis Cloud (free 30 MB).
- Decision: Define `VectorStore` interface. Default backend Qdrant (local mode for dev/CI, Qdrant Cloud free for hosted). Pinecone and Upstash adapters as optional.
- Consequences: If namespaces are used for scoping, Pinecone/Upstash free tiers cap distinct fingerprints at 100 per index; adapters for those must scope with metadata filters instead.

## ADR-005: Semantic cache scoped by context fingerprint + tenant
- Date: 2026-09-25 · Status: Accepted
- Context: Classic semantic-cache bug is returning an answer from a different system prompt / conversation.
- Decision: Lookups filtered by fingerprint(model, system, tools, policy_version) and tenant_hash.
- Consequences: Lower hit rate, much higher correctness. Changing the system prompt naturally invalidates.

## ADR-006: Dual hashing (raw + canonical)
- Date: 2026-09-25 · Status: Accepted
- Context: Provider caches match on the rendered prompt, so serialization changes (key order, whitespace) can bust cache even when meaning is identical.
- Decision: Store both hashes per segment.
- Consequences: Enables the "serialization nondeterminism" diagnosis; slightly more storage.

## ADR-007: No Supabase
- Date: 2026-09-25 · Status: Accepted
- Context: Harris chose to move off Supabase for this project.
- Decision: Plain SQLAlchemy against SQLite/Postgres; no RLS dependency.
- Consequences: Tenant scoping enforced in application queries.

## ADR-008: Local embeddings by default
- Date: 2026-09-25 · Status: Proposed
- Context: Free, low latency, no extra API key; must fit vector store dimension limits.
- Decision: Default to a small local embedding model; `Embedder` interface allows API models.
- Consequences: Model download on first run; pin model version in config.

## ADR-009: Fail-open gateway
- Date: 2026-09-25 · Status: Accepted
- Context: A diagnostics tool must never break the thing it observes.
- Decision: All non-forwarding work is wrapped and runs after the client response where possible.
- Consequences: Some requests may be missing analysis; record `analysis_error` counts.

## ADR-010: Qdrant confirmed; hosted on Qdrant Cloud
- Date: 2026-09-25 · Status: Accepted (resolves ADR-004)
- Context: Harris confirmed Qdrant and provisioned a Qdrant Cloud cluster.
- Decision: Qdrant is the default vector backend. Dev/CI use local in-process mode or `memory`; the hosted cluster is used via `PCG_VECTOR_URL` + `PCG_VECTOR_API_KEY`, supplied only through a gitignored `.env`. Pinecone/Upstash remain optional adapters.
- Consequences: The key must never appear in docs, fixtures, DB or logs (SECURITY G-2). CI must not need the cloud cluster. Cluster URL still to be recorded in `.env` (not in docs).

## ADR-014: GitHub Actions CI/CD and PR-per-milestone workflow
- Date: 2026-09-27 · Status: Accepted
- Context: Codex implements, Claude audits; needs an enforced, reviewable trail per change.
- Decision: GitHub is the remote. Every milestone is one branch (`phase-N/mN.M-slug`) and one PR into `main`. `main` is protected: PR required, required status checks green, no direct pushes. GitHub Actions runs the standard gate commands, secret scan and dependency audit on every PR; a deploy workflow builds and publishes the Docker image on version tags (Phase 6). Live-provider and Qdrant Cloud tests run only via manual `workflow_dispatch` using repo secrets, never on PRs from forks.
- Consequences: Claude's audit is recorded as a PR review comment before merge; gate reviews (`reviews/phase-N-gate.md`) are committed in the phase's final PR. CI must pass without any cloud credentials. Deploy target (registry/host) is decided at M6.3.

## ADR-011: Wire-format-family adapters and a canonical request model
- Date: 2026-09-27 · Status: Accepted (amends ADR-002 phasing)
- Context: Goal is to work with any LLM. Vendors share a few wire protocols; adapting per vendor would explode.
- Decision: One adapter per wire format (anthropic-messages, openai-chat, openai-responses, gemini). Adapters convert to a `CanonicalRequest`; all core components (segmenter, hasher, lineage, diff, classifier, fingerprint, eligibility) see only canonical types. Adapter contract: `parse_request`, `forward`, `extract_usage`, `cache_profile`, `is_cacheable_response`, `render_response`, `synthesize_sse`, auth header names.
- Consequences: No provider shapes in core. OpenAI-chat moves to Phase 2 to prove the abstraction early and covers Azure, Groq, Together, Mistral, DeepSeek, xAI, OpenRouter, vLLM, Ollama, etc. A shared adapter conformance suite gates every adapter.

## ADR-012: Upstream registry and routing
- Date: 2026-09-27 · Status: Accepted
- Context: Multiple providers require routing, but REQ-SEC-03 forbids user-controlled upstream URLs.
- Decision: `PCG_UPSTREAMS` maps name to `{wire_format, base_url, auth_style}`, validated against an allowlist at startup. Clients choose by `/u/{name}/...` or `x-pcg-upstream`. Bare `/v1/messages` and `/v1/chat/completions` use `PCG_DEFAULT_UPSTREAM`. Client keys forwarded unchanged.
- Consequences: No failover or load balancing. Fingerprint includes `wire_format` and `upstream`, so semantic hits never cross formats and no translation layer is needed.

## ADR-013: CacheProfile and NOT_REPORTED
- Date: 2026-09-27 · Status: Accepted
- Context: Providers differ: Anthropic has explicit breakpoints; OpenAI/DeepSeek/Gemini-implicit cache prefixes automatically; some (local models) report nothing.
- Decision: Each adapter exposes a `CacheProfile` (`cache_mode`: EXPLICIT_BREAKPOINT | AUTO_PREFIX | NONE; `min_cacheable_tokens(model)`; `default_ttl_s`; `usage_reporting`: full | read_only | none; `segment_order`). Usage cache fields are nullable (unknown ≠ 0). New class `NOT_REPORTED`. The classifier selects rules by cache_mode.
- Consequences: Detector still gives hash diffs and lineage for providers that report nothing. Schema gains `wire_format`, `upstream`, `cache_mode` on requests and nullable cache token columns.
