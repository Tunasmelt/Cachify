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
| Anthropic Messages | `anthropic-version: 2023-06-01` | `/v1/messages`, `cache_control`, usage `cache_creation_input_tokens` / `cache_read_input_tokens`, SSE events | Verified 2026-10-03 against platform.claude.com prompt-caching docs: default TTL 5 min, optional 1h (`"ttl":"1h"`, 2x input price); TTL measured from request start. Minimum cacheable length is per model: 512 (Fable 5.x, Opus 5.x, Sonnet 5.5), 1024 (Opus 4/4.1, Sonnet 4/4.5/4.6, Opus 4.8, Sonnet 5), 2048 (Mythos Preview, Opus 4.7, Haiku 3.5), 4096 (Opus 4.5/4.6, Haiku 4.5); shorter prompts are processed uncached with no error. Max 4 `cache_control` breakpoints per request (exceeding returns 400). Usage fields: `cache_creation_input_tokens`, `cache_read_input_tokens`, `input_tokens` (uncached). Caching is opt-in: top-level automatic `cache_control` or per-block explicit breakpoints. Re-verify before each change to the model table. |
| OpenAI Chat Completions | TBD | `/v1/chat/completions`, SSE `data:` chunks + `[DONE]`, `usage.prompt_tokens_details.cached_tokens`, min cacheable length | Not yet: verify before Phase 2. |
| OpenAI-compatible providers (Azure, Groq, Together, Mistral, DeepSeek, xAI, OpenRouter, vLLM, Ollama) | TBD | per-provider cache usage fields (may be absent, then NOT_REPORTED) | Not yet: verify per provider before relying on it. |
| OpenAI Responses | TBD | `/v1/responses`, cached-token usage | Not yet: verify before Phase 5. |
| Google Gemini | TBD | `generateContent`, `streamGenerateContent`, implicit caching usage (`cachedContentTokenCount`) | Not yet: verify before Phase 5. |
| Qdrant | client version pinned in lockfile | collections, payload filters, local mode | Free-tier facts checked 2026-09-25 (1 GB free cluster). |
| Pinecone (optional) | SDK pinned | serverless index, namespaces, metadata filter | Free-tier facts checked 2026-09-25 (Starter: 2 GB, 5 indexes, 100 namespaces/index, ~40 KB metadata). |
| Upstash Vector (optional) | SDK pinned | namespaces, metadata, data field | Free-tier facts checked 2026-09-25 (10K queries/day, 100 namespaces, 1536 max dims, 48 KB metadata). |

Rule: any code depending on an external fact above must reference this table; update the date when re-verified.
