---
doc: CHANGELOG
status: append-only
last_verified_commit: none (pre-repo)
---

# Changelog

## 2026-09-25
- Project kicked off: cache-bust detector + semantic response cache combined into one gateway.
- Initial doc set created (PRODUCT, ARCHITECTURE, DECISIONS, PHASES, SECURITY, API, HANDOFF).
- Supabase dropped (ADR-007). Vector store proposed as Qdrant behind an interface (ADR-004, pending).
- Git repo initialised. `.gitignore` added (covers `.env`, `.claude/`).
- Qdrant confirmed and hosted on Qdrant Cloud (ADR-010). API key kept in gitignored `.env`, not in docs.

## 2026-09-27
- Multi-LLM support designed: wire-format-family adapters + canonical model (ADR-011), upstream registry (ADR-012), CacheProfile and NOT_REPORTED class (ADR-013).
- GitHub Actions CI/CD and PR-per-milestone workflow added (ADR-014): ci.yml, integration.yml (manual), dashboard.yml, release.yml; branch protection; Claude audit as PR review.
- PRODUCT (REQ-GW-01/07/08, REQ-BD-06, A1/A2, non-goals), ARCHITECTURE, PHASES (M1.0, M2.4, M5.3/5.4), API, SECURITY updated. OpenAI-chat moved to Phase 2; Gemini native in Phase 5; Bedrock/Vertex and cross-format translation deferred.
- GitHub repo created (Tunasmelt/Cachify), initial docs pushed to `main`.
- M0.1 (repo skeleton) merged: uv/hatchling project, `src/pcg`, ruff/mypy/pytest config, pre-commit, `.gitattributes`, `.env.example`.
- MIT LICENSE added; stale `docs/prompt-cache-gateway-docs.zip` removed.
- M0.2 (config loader) merged: pydantic-settings `Settings` covering every ARCHITECTURE.md env var, typed upstream config, secret masking, cached `get_settings()`.
- Orchestration workflow tightened: Codex implements only (no git writes, no self-audit); Claude independently verifies Codex's transcript and gate results before committing, since a low-reasoning-effort run previously reported success while writing nothing. Codex reasoning effort raised to medium globally.
- M0.3 (GitHub Actions CI): `ci.yml`, `gitleaks.yml`, `pip-audit.yml`, `integration.yml` (manual-only), PR template, CODEOWNERS, Dependabot. Actions pinned by commit SHA.
- Fixed `gitleaks-action@v3`'s breaking change (requires `GITHUB_TOKEN` explicitly on `pull_request` events), caught by the first live CI run.
- Branch protection enabled on `main`: PR required, `ci`/`gitleaks`/`pip-audit` required status checks, `enforce_admins`, no force-push/deletion.
- Phase 0 complete. First Dependabot PR (action SHA bump) audited and merged.
- M1.1 (non-streaming Anthropic passthrough) merged; M1.2 (SSE streaming passthrough with stream tee) merged. Model-matcher fix for `claude-3-5-haiku-*` IDs applied during review.
- M1.3 (per-request records): `requests` table via Alembic, background writes with HMAC tenant hashing (ADR-015), fail-open. `PCG_TENANT_HASH_SALT` added.
- M1.4 (fail-open wrapper): analysis steps run behind `guard`/`aguard` (src/pcg/failopen.py) with per-component failure counts exposed on `app.state.analysis_errors`. Unexpected parse errors forward via a stream probe instead of returning 500; ValueError stays a 400. Failure logs carry component and exception class only.

## 2026-10-24
- Gate G1 reviewed: standard gates and `pytest -m proxy` pass on `f2db082`; `reviews/phase-1-gate.md` added. Live Anthropic check deferred (no integration test or repo secrets yet). HANDOFF updated; Phase 2 next.
- M2.1 (segmenter + dual hasher): segments are now sliced byte-exact from the request body (`pcg/segmenter.py`), replacing the M1.0 re-serialization. `raw_hash` (exact bytes) and `canon_hash` (sorted compact JSON) computed in `pcg/hashing.py`; `segments` table added (migration 0002) and written with the request row, hashes only, fail-open. Non-UTF-8 request bodies are now rejected with 400.
- M2.1 merged (#15). M2.2 (lineage): `predecessor_id` and `lineage_source` on `requests` (migration 0003), resolved at insert time in `pcg/lineage.py`. `x-pcg-session` wins (latest same-tenant request in the session, any model); otherwise longest common `raw_hash` prefix among the last 50 same-tenant, same-model requests within 1 hour (`PCG_LINEAGE_WINDOW_REQUESTS`, `PCG_LINEAGE_WINDOW_S`), ties to the most recent. Fail-open. Resolves OQ-3 defaults.
- M2.2 merged (#16). M2.3 (hash-level): `pcg/diff.py` finds the first segment where `raw_hash` differs from the predecessor and classifies it (`none`, `append`, `truncated`, `serialization` = raw differs/canon equal, `content`). Stored as `divergence_index` and `divergence_kind` on `requests` (migration 0004), computed at insert time, fail-open. Diff excerpts (needs predecessor text, only in `PCG_LOG_MODE=full`) deferred pending a storage decision.
- Fixed: `migrations/env.py` called `fileConfig` with the default `disable_existing_loggers=True`, so `migrate()` at startup silenced all `pcg.*` loggers (fail-open warnings, gateway logs) since M1.3. Now `False`, with a regression test.
- 2026-11-01: `reviews/phase-0-gate.md` backfilled. G0 re-verified against the repo; the 'failing check blocks merge' requirement, previously unrecorded, was verified with throwaway PR #19 (closed unmerged). `main` briefly failed pip-audit on a new `msgpack` advisory; fixed by #20.
- M2.4 (openai-chat adapter): `OpenAIChatAdapter` (byte-exact segments in wire order, `AUTO_PREFIX`, read-only usage), OpenAI SSE stream parser, and a wire-format registry (`pcg/wire.py`) so the gateway no longer hard-codes Anthropic. New routes `/v1/chat/completions` and `/u/{name}/v1/chat/completions`; an endpoint/upstream wire-format mismatch now returns 400 (previously 501 for unimplemented formats). Usage is normalized to uncached input (ADR-016). Golden request pairs now cover both formats. API.md OpenAI row partially verified; TTL still a placeholder.
- 2026-11-01: M2.4 merged (#23) with #20, #21, #22 and Dependabot #18. `reviews/phase-2-gate.md` added: G2 is a conditional pass (all tests pass; REQ-BD-04's text diff excerpt deferred to M3.2). Phase 2 closed.
