# Cachify

![CI](https://github.com/Tunasmelt/Cachify/actions/workflows/ci.yml/badge.svg)
![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)

**A drop-in LLM gateway that explains why your prompt cache missed, and (soon) skips the model call
when a near-identical question was already answered.**

Cachify (package name `pcg`, "Prompt Cache Gateway") sits between your agents and an LLM provider.
You change one base URL; requests pass through unchanged while Cachify records what was sent and
how the provider's cache responded.

> **Status: early development (Phase 2 of 6 complete).** The proxy, request recording, lineage and
> divergence detection work today. The classifier, CLI, semantic cache and dashboard are not built
> yet. See [Roadmap](#roadmap). Not ready for production use.

## Why

Provider prompt caching (Anthropic, OpenAI and others) makes long, repeated prefixes cheap, but only
on an **exact prefix match**. One changed byte early in the prompt (a timestamp in the system prompt,
a reordered tool list, a re-serialized JSON key) silently disables the cache, and nothing tells you
which byte. Cachify is built to answer that question, and then to avoid the call entirely when the
answer is already known.

## What it does

Cachify has two components behind one proxy:

1. **Bust detector.** Splits each request into ordered segments (tools, system, messages), hashes
   every segment twice, links the request to its predecessor, and finds the first segment that
   changed.
   - `raw_hash`: SHA-256 of the exact bytes the client sent.
   - `canon_hash`: SHA-256 of canonical JSON (sorted keys, no insignificant whitespace).
   - If the raw hash changed but the canonical hash did not, the cache broke on serialization alone.
2. **Semantic response cache** *(planned, Phase 4).* Context-scoped, embedding-based response reuse
   with eligibility rules and expiry.

### What works today

| Capability | State |
|---|---|
| Anthropic Messages passthrough (`/v1/messages`), streaming and non-streaming | Working |
| OpenAI Chat Completions passthrough (`/v1/chat/completions`), streaming and non-streaming | Working |
| Named, allowlisted upstreams (clients never supply URLs) | Working |
| Fail-open: an analysis or recording failure never breaks the upstream response | Working |
| Per-request records with usage (input, cache read, cache write, output tokens) | Working |
| Byte-exact segments with raw and canonical hashes | Working |
| Lineage: link a request to its predecessor by session header or longest-prefix inference | Working |
| First-divergence index and kind (`append`, `truncated`, `serialization`, `content`) | Working |
| Bust classification and diagnosis (HEALTHY, COLD_START, TTL_EXPIRED, BUST, ...) | Planned (Phase 3) |
| `pcg report` / `pcg busts` CLI, readable diff excerpts | Planned (Phase 3) |
| Semantic cache with replay (JSON and SSE) | Planned (Phase 4) |
| Admin API, dashboard, OpenAI Responses and Gemini adapters | Planned (Phase 5) |

## Quick start

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/Tunasmelt/Cachify.git
cd Cachify
uv sync
```

Configure through environment variables or a `.env` file (see `.env.example`). The minimum is one
upstream:

```sh
export PCG_UPSTREAMS='{"anthropic":{"wire_format":"anthropic-messages","base_url":"https://api.anthropic.com","auth_style":"x-api-key"}}'
export PCG_DEFAULT_UPSTREAM=anthropic
# Required to record requests; at least 32 random characters. Without it the gateway
# still proxies, but writes no records.
export PCG_TENANT_HASH_SALT="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
```

Start the gateway. There is no `pcg serve` command yet, so for now it is launched through uvicorn:

```sh
uv run python -c "import uvicorn; from pcg.config import get_settings; from pcg.gateway import create_app; uvicorn.run(create_app(get_settings()), host='127.0.0.1', port=8080)"
```

Point your client at it by changing only the base URL. Your own provider key is forwarded upstream
and is never stored or logged:

```sh
curl http://127.0.0.1:8080/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{"model":"claude-sonnet-5-5","max_tokens":64,"messages":[{"role":"user","content":"Hello"}]}'
```

To proxy an OpenAI-compatible provider as well, add a second upstream with
`"wire_format":"openai-chat"` and `"auth_style":"bearer"`, then call
`/u/<name>/v1/chat/completions`. An endpoint whose wire format does not match the chosen upstream
returns `400`.

### Routes and headers

| Route | Wire format |
|---|---|
| `POST /v1/messages`, `POST /u/{upstream}/v1/messages` | `anthropic-messages` |
| `POST /v1/chat/completions`, `POST /u/{upstream}/v1/chat/completions` | `openai-chat` |

| Request header | Effect |
|---|---|
| `x-pcg-upstream: <name>` | Select a registered upstream instead of the default. Unknown names are rejected. |
| `x-pcg-session: <id>` | Explicit lineage: the predecessor is the latest request in that session. |

Without a session header, the predecessor is inferred as the recent request (same tenant and model)
with the longest shared prefix of segment hashes.

### What gets recorded

Records go to SQLite by default (`PCG_DB_URL`), with the schema managed by Alembic and applied at
startup. Only **hashes and metadata** are stored, never prompt text or API keys:

- `requests`: upstream, wire format, cache mode, model, status, token usage, latency, an HMAC
  `tenant_hash` of the client key, predecessor, and divergence index/kind.
- `segments`: per request and per segment, kind, role, `raw_hash`, `canon_hash`, byte length, and
  whether it carries a cache breakpoint.

Token usage is normalized across providers: `input_tokens` always means **uncached** input
([ADR-016](docs/DECISIONS.md)). Cache fields the provider does not report are stored as `NULL`
(unknown), not zero.

## Configuration

| Variable | Purpose |
|---|---|
| `PCG_UPSTREAMS` | JSON map of named upstreams: `{name: {wire_format, base_url, auth_style}}`. |
| `PCG_DEFAULT_UPSTREAM` | Upstream used by the bare routes. |
| `PCG_TENANT_HASH_SALT` | Secret, 32+ characters. Enables request records. |
| `PCG_DB_URL` | SQLite path or Postgres URL (default `sqlite:///./pcg.db`). |
| `PCG_LOG_MODE` | `off` \| `hash` \| `full` (default `hash`). |
| `PCG_LINEAGE_WINDOW_REQUESTS`, `PCG_LINEAGE_WINDOW_S` | Prefix-inference window (default 50 requests / 3600 s). |

The remaining variables in `.env.example` (vector backend, embeddings, semantic-cache thresholds,
admin token) are reserved for later phases. The full table is in
[ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Roadmap

Delivery is one milestone per branch and pull request, with a written gate review at the end of each
phase (`reviews/`). 13 of 28 milestones are done.

| Phase | Scope | Status |
|---|---|---|
| 0 | Bootstrap: repo skeleton, config loader, CI | Done |
| 1 | Passthrough proxy: Anthropic, streaming, request records, fail-open | Done (gate: conditional, live check pending) |
| 2 | Segmentation, lineage, first-divergence diff, openai-chat adapter | Done (gate: conditional, readable diff excerpt deferred) |
| 3 | Classification of every request (HEALTHY / BUST / TTL_EXPIRED / ...), diagnosis rules, CLI | Next |
| 4 | Semantic response cache (embeddings, vector store, SSE replay, expiry) | Planned |
| 5 | Admin API, dashboard, OpenAI Responses and Gemini adapters | Planned |
| 6 | Bust-trace corpus, latency benchmark, Docker and deploy | Planned |

## Development

```sh
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy src/
uv run pytest -q
uv run python -c "import pcg"
```

The suite runs offline against recorded fixtures and mock transports. Tests that call a live provider
are marked `integration` and are skipped unless run with `--anthropic-live` (they need
`PCG_ANTHROPIC_API_KEY`):

```sh
uv run pytest -m integration --anthropic-live
```

CI (`ci`, `gitleaks`, `pip-audit`) is required on every pull request. Secret scanning also runs in
pre-commit (`uv run pre-commit install`).

## Documentation

| Doc | Contents |
|---|---|
| [PRODUCT.md](docs/PRODUCT.md) | Problem, scope, requirements (REQ-IDs), open questions |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, data model, configuration |
| [DECISIONS.md](docs/DECISIONS.md) | Architecture decision records |
| [PHASES.md](docs/PHASES.md) | Milestones and gates |
| [API.md](docs/API.md) | Gateway routes and the external provider facts relied on |
| [SECURITY.md](docs/SECURITY.md) | Threat model and per-gate checklist |
| [HANDOFF.md](docs/HANDOFF.md) | Current state and next action |
| [CHANGELOG.md](docs/CHANGELOG.md) | What changed, in order |

## Contributing

Use one branch and one pull request per milestone (`phase-N/mN.M-slug`). Pull requests into `main`
are required and CI must be green before merge. Each PR states its milestone, the REQ-IDs it covers,
the tests it adds, and the security checklist items it touches.

## License

MIT. See [LICENSE](LICENSE).
