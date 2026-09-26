---
doc: API
status: draft
last_verified_commit: none (pre-repo)
updated: 2026-09-25
---

# API

## Own API
Generated from OpenAPI (`/openapi.json`) once Phase 5 lands. Until then, planned surface:

### Proxy
| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/v1/messages` | Provider key (forwarded) | anthropic-messages, streaming supported. |
| POST | `/v1/chat/completions` | Provider key (forwarded) | openai-chat (Phase 2). |
| POST | `/v1/responses` | Provider key (forwarded) | openai-responses (Phase 5). |
| POST | `/v1beta/models/{model}:generateContent`, `:streamGenerateContent` | Provider key (forwarded) | gemini (Phase 5). |

All proxy routes are also available under `/u/{upstream}/...` to select a named upstream from `PCG_UPSTREAMS`.

Request headers (optional):
- `x-pcg-upstream: <name>`: select a registered upstream (unknown names rejected; no URLs accepted).
- `x-pcg-session: <id>`: explicit lineage.
- `x-pcg-cache: bypass`: skip semantic cache lookup and write.
- `x-pcg-log: off|hash|full`: per-request log mode (cannot exceed server max).
- `x-pcg-tags: a,b`: tags on any cache entry written.

Response headers:
- `x-pcg-request-id`
- `x-pcg-semantic: hit|miss|bypass|ineligible`
- `x-pcg-semantic-score` (on hit)

### Admin (`PCG_ADMIN_TOKEN` bearer)
| Method | Path | Purpose |
|---|---|---|
| GET | `/pcg/health` | Liveness (no auth). |
| GET | `/pcg/requests` | Paginated request records, filters: session, class, since. |
| GET | `/pcg/requests/{id}` | Detail incl. segments and diff. |
| GET | `/pcg/busts` | Bust events with diagnosis. |
| GET | `/pcg/metrics` | Hit ratios, savings estimates. |
| POST | `/pcg/cache/invalidate` | Body: `{fingerprint?, tags?, ids?}`. |

## External APIs

| API | Pinned version | What we use | Last verified |
|---|---|---|---|
| Anthropic Messages | `anthropic-version: 2023-06-01` | `/v1/messages`, `cache_control`, usage `cache_creation_input_tokens` / `cache_read_input_tokens`, SSE events | Not yet: verify against current docs before Phase 1 (TTL options, min cacheable length per model, breakpoint limits). |
| OpenAI Chat Completions | TBD | `/v1/chat/completions`, SSE `data:` chunks + `[DONE]`, `usage.prompt_tokens_details.cached_tokens`, min cacheable length | Not yet: verify before Phase 2. |
| OpenAI-compatible providers (Azure, Groq, Together, Mistral, DeepSeek, xAI, OpenRouter, vLLM, Ollama) | TBD | per-provider cache usage fields (may be absent, then NOT_REPORTED) | Not yet: verify per provider before relying on it. |
| OpenAI Responses | TBD | `/v1/responses`, cached-token usage | Not yet: verify before Phase 5. |
| Google Gemini | TBD | `generateContent`, `streamGenerateContent`, implicit caching usage (`cachedContentTokenCount`) | Not yet: verify before Phase 5. |
| Qdrant | client version pinned in lockfile | collections, payload filters, local mode | Free-tier facts checked 2026-09-25 (1 GB free cluster). |
| Pinecone (optional) | SDK pinned | serverless index, namespaces, metadata filter | Free-tier facts checked 2026-09-25 (Starter: 2 GB, 5 indexes, 100 namespaces/index, ~40 KB metadata). |
| Upstash Vector (optional) | SDK pinned | namespaces, metadata, data field | Free-tier facts checked 2026-09-25 (10K queries/day, 100 namespaces, 1536 max dims, 48 KB metadata). |

Rule: any code depending on an external fact above must reference this table; update the date when re-verified.
