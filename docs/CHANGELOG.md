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
