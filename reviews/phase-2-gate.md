---
doc: phase-2-gate
status: conditional pass (diff excerpts deferred to M3.2)
commit: 60f0607
date: 2026-11-01
---

# Gate G2: Segmentation, lineage, diff

Result: **conditional pass.** Everything G2 asks to be tested passes except the human-readable half of REQ-BD-04: the engine finds the first divergent segment and classifies it, but produces no text diff excerpt. That was deferred to M3.2 (option C, decided 2026-10-24) because the predecessor's segment text is not stored today. The security check about excerpts is therefore vacuously true: no content is stored in any log mode.

## Commands (run on `60f0607`)
| Command | Result |
|---|---|
| `ruff check .` | pass |
| `ruff format --check .` | pass (59 files) |
| `mypy src/` | pass (18 source files) |
| `pytest -q` | pass (189 passed, 4 skipped: the live Anthropic tests, which need `--anthropic-live`) |
| `pytest -m detector` | pass (52 passed) |
| `pip-audit` | no known vulnerabilities |

## Tests
| Requirement | Evidence |
|---|---|
| Golden request pairs (identical, tool reorder, timestamp in system, edited earlier message, key-order-only change) give the expected divergence index and raw/canon result, anthropic | `tests/test_diff.py` (also whitespace-only, append, truncation, earliest-difference-wins) |
| Same pairs for openai-chat | `tests/test_diff_openai_chat.py` (same cases, wire-order segments, `system`/`developer` messages) |
| openai-chat adapter passes the conformance suite | `tests/test_openai_chat_adapter.py::test_openai_chat_adapter_conforms`; the Anthropic adapter and the fake adapter still pass theirs |
| Segments are byte-exact and hashed twice (REQ-BD-01, 02) | `tests/test_segmenter_hashing.py` (exact bytes, ordering, breakpoints, known SHA-256 vector, raw-differs/canon-equal cases), `tests/test_gateway_records.py` (hashes persisted, no prompt text or key in the DB) |
| Lineage via session header, then prefix inference (REQ-BD-03) | `tests/test_lineage.py` (longest prefix, tie to most recent, tenant and model strictness, window by count and time, session wins, session is tenant-scoped, fail-open), plus gateway end-to-end tests for both formats |
| openai-chat endpoint (REQ-GW-01) | `tests/test_gateway_openai_chat.py` (byte-identical stream, 4xx/5xx passthrough, header allowlist, route/upstream mismatch, normalized usage, bearer-token tenant hash) |

## Security
- [x] Diff excerpts respect `PCG_LOG_MODE`: **vacuous.** No excerpt exists; only hashes, indexes and kinds are stored, in every log mode (`grep` finds no excerpt code in `src/`).
- [x] Tenant isolation: lineage never links across tenants (tested for session and inference paths).
- [x] Provider key absent from DB and logs for both auth styles (x-api-key and bearer). These log checks now run against real log output, after the M2.3 fix to `migrations/env.py` that had been silencing application loggers.
- [x] openai-chat forwards only `authorization`, `content-type`, `openai-organization` and `openai-project`.

## Covers
REQ-BD-01, REQ-BD-02, REQ-BD-03, REQ-GW-01 (openai-chat): **complete.**
REQ-BD-04: **partial** (divergence index and kind; excerpt pending M3.2).

## Open items
- **REQ-BD-04 excerpt** (M3.2): decide where the predecessor's text lives (in-memory LRU, persisted in `full` mode, or bust events).
- **`PCG_LOG_MODE=off`** still stores hashes and lineage. ARCHITECTURE.md's data model allows that, but confirm it is the intent.
- **OpenAI facts to verify before Phase 3:** cache retention TTL (adapter uses an unverified 300 s placeholder), `stream_options.include_usage` behavior, and OpenAI-compatible providers' cache fields.
- **G1 live check** still pending: needs the `PCG_ANTHROPIC_API_KEY` repo secret and a run of `integration.yml`.
- **Retention** for `requests` and `segments` rows is not defined until Phase 6.
