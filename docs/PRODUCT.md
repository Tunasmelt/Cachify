---
doc: PRODUCT
project: prompt-cache-gateway (working name)
status: draft
last_verified_commit: none (pre-repo)
updated: 2026-09-25
---

# Product: Prompt Cache Gateway

## One-liner
A drop-in LLM gateway that (1) tells you exactly why provider-side prompt caching missed on a request, and (2) skips the LLM call entirely when a near-identical question has already been answered in the same context.

## Problem
- Agents resend large, mostly-identical prefixes (system prompt, tool definitions, retrieved docs, history) on every turn. Provider prompt caching makes that cheap, but only on an exact prefix match. One changed byte early in the prompt silently disables it, and nothing tells you which byte.
- Many requests (FAQ-style, repeated eval runs, retries, near-duplicate user questions) could be answered from a previous response without calling the model at all.

Plain version: the gateway is a front desk. It hands back old answers when the question is basically the same, and when it does have to ask the model, it checks whether the "you've already read this part" discount applied, and explains why when it didn't.

## Users
- U1: Solo developer / small team running agents against the Anthropic API (primary: Harris's own agents).
- U2: Engineer debugging LLM cost or latency regressions.

## Scope (v1)
Two components behind one proxy:
1. **Bust Detector**: segment, hash, and diff consecutive requests; correlate with provider-reported cache usage; classify and explain misses.
2. **Semantic Response Cache**: context-scoped, embedding-based response reuse with eligibility rules and expiry.

## Requirements

### Gateway (GW)
| ID | Requirement |
|---|---|
| REQ-GW-01 | Expose provider-native compatible endpoints per wire format (anthropic-messages, openai-chat, openai-responses, gemini) so clients switch by changing base URL only. See ADR-011. |
| REQ-GW-02 | Support streaming (SSE) passthrough with no buffering of the client-facing stream; tee the stream for internal parsing. |
| REQ-GW-03 | Forward client API keys upstream unchanged; never persist them in plaintext. |
| REQ-GW-04 | Add no more than 15 ms p95 overhead on the cache-miss path (excluding embedding when semantic cache is enabled). |
| REQ-GW-05 | Per-request opt-out headers: bypass semantic cache, disable body logging. |
| REQ-GW-06 | Gateway must fail open: if internal components (logging, detector, cache) error, the upstream request still completes. |
| REQ-GW-07 | Upstream registry: named, allowlisted upstreams (`PCG_UPSTREAMS`), each with a wire format, base URL and auth style. Clients select one by path prefix `/u/{name}/` or `x-pcg-upstream`; clients never supply URLs. See ADR-012. |
| REQ-GW-08 | Normalize provider usage to input, output, cache write, cache read. Fields the provider does not report are `null` (unknown), never 0. |

### Bust Detector (BD)
| ID | Requirement |
|---|---|
| REQ-BD-01 | Split each request into ordered segments matching provider cache prefix order (Anthropic: tools, system, messages). |
| REQ-BD-02 | Compute two hashes per segment: raw (exact serialized bytes) and canonical (normalized JSON). |
| REQ-BD-03 | Link each request to its predecessor ("lineage") via explicit session header, falling back to longest-common-prefix inference. |
| REQ-BD-04 | Find the first divergent segment vs. predecessor and produce a human-readable diff. |
| REQ-BD-05 | Record provider usage: input, cache write, cache read, output tokens. |
| REQ-BD-06 | Classify each request: HEALTHY, COLD_START, TTL_EXPIRED, BELOW_MIN_LENGTH, BUST, UNEXPLAINED, NOT_REPORTED (provider reports no cache usage). Rules adapt to the provider's cache mode (ADR-013). |
| REQ-BD-07 | For BUST, attach a diagnosis from a rule set: timestamp/UUID in prefix, tool reorder, serialization nondeterminism (raw differs, canonical equal), history rewrite/summarization, model change, breakpoint after volatile content. |
| REQ-BD-08 | CLI report: per-session hit rate, busts with diffs, estimated tokens/cost lost. |

### Semantic Cache (SC)
| ID | Requirement |
|---|---|
| REQ-SC-01 | Compute a context fingerprint = hash(model, system, tools, cache-scope policy inputs). Lookups only match within the same fingerprint. |
| REQ-SC-02 | Embed the final user turn (optionally plus a conversation summary hash) and query the vector store scoped to the fingerprint. |
| REQ-SC-03 | Return a hit only above a configurable similarity threshold (global default, per-route override). |
| REQ-SC-04 | Eligibility: never cache responses containing tool_use, errors, or stop_reason other than end_turn; skip temperature > configured max unless opted in. |
| REQ-SC-05 | Every entry has an expiry; expired entries are never served and are purged by a cleanup job. |
| REQ-SC-06 | Tag-based and fingerprint-based invalidation via admin API. |
| REQ-SC-07 | On a hit, return a provider-shaped response (including SSE replay when the client requested streaming) with header marking it as a semantic hit. |
| REQ-SC-08 | Vector store accessed only through a VectorStore interface so backends are swappable. |

### Observability (OBS)
| ID | Requirement |
|---|---|
| REQ-OBS-01 | Persist per-request records: timing, usage, classification, semantic hit/miss, similarity score. |
| REQ-OBS-02 | Metrics: provider cache hit ratio, semantic hit ratio, estimated savings, bust count by diagnosis. |
| REQ-OBS-03 | Dashboard (Phase 5) showing the above plus a bust timeline with diffs. |

### Security (SEC)
| ID | Requirement |
|---|---|
| REQ-SEC-01 | Gateway admin/dashboard endpoints require a gateway token separate from provider keys. |
| REQ-SEC-02 | Prompt/response body logging is configurable (off / hashes-only / full) with retention limit. |
| REQ-SEC-03 | Upstream host allowlist; no user-controlled upstream URLs. |
| REQ-SEC-04 | Semantic cache entries are scoped by tenant (API key hash) in addition to context fingerprint. |

## Non-goals (v1)
- Not a general LLM router, load balancer, or multi-provider failover (multiple upstreams are selected explicitly by the client; no automatic routing).
- No cross-format translation (e.g. an Anthropic-format client talking to an OpenAI upstream) in v1.
- Bedrock and Vertex are deferred: request signing (SigV4/OAuth) conflicts with forwarding client keys unchanged (REQ-GW-03).
- Not a hosted multi-tenant SaaS.
- No caching of tool-use turns or agent actions.
- No modification of client prompts (the gateway diagnoses; it does not auto-fix). Auto-fix suggestions may come later.
- No prompt compression or summarization.

## Assumptions
- A1: Any LLM is supported via wire-format-family adapters. Anthropic first (Phase 1), OpenAI-chat next (Phase 2, covers most compatible providers), OpenAI-responses and Gemini native in Phase 5.
- A2: Most providers report cache token counts in usage; those that do not are classified NOT_REPORTED (ADR-013).
- A3: Single-node deployment is sufficient for v1 traffic.
- A4: Free-tier infrastructure only during development.

## Open questions
- OQ-1: RESOLVED: Qdrant (ADR-010), hosted on Qdrant Cloud.
- OQ-2: Embedding model: local (e.g. a small sentence-transformers / fastembed model) vs. API embeddings. Local keeps it free and fast; dimension must fit the chosen store's free-tier limit.
- OQ-3: Lineage inference window: how many recent requests to compare, and over what time window.
- OQ-4: Should semantic cache consider more than the final user turn for multi-turn conversations (e.g. last N turns hashed into the fingerprint)?
- OQ-5: Final project name.
