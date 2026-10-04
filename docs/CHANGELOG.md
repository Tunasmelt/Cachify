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

