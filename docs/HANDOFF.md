---
doc: HANDOFF
status: overwritten each session
last_verified_commit: none (pre-repo)
updated: 2026-09-27
---

# Handoff

**Current phase:** Phase 0 (Bootstrap), not started. Docs are updated for multi-LLM support (ADR-011..013). Git repo initialised, nothing committed.

**Orchestration:** Claude Code orchestrates; Codex CLI (`codex exec`, v0.157.0, logged in) implements one milestone at a time; Claude audits after each milestone/gate and writes `reviews/phase-N-gate.md`.

**Workflow (ADR-014):** one branch + one PR per milestone; Codex opens the PR, Claude audits it (PR review) before squash merge; GitHub Actions must be green. Needs a GitHub remote (`gh repo create`), not yet created.

**Next action:** Scaffold `src/pcg/` (uv, FastAPI, pydantic-settings, CI running the standard gate commands), then M1.0 (canonical model + adapter contract) before M1.1.

**Blockers / pending decisions:**
- GitHub repo: create remote (name, public/private), set repo secrets (Qdrant, provider keys for integration only), enable branch protection. Deploy target decided at M6.3.
- ADR-008 embedding model: confirm local model (default proposal: fastembed bge-small, 384 dims); the Qdrant collection vector size depends on it.
- Qdrant Cloud: cluster URL and API key are in the local gitignored `.env`; connection not yet tested. The key was pasted into chat, so rotate it.
- Re-verify provider caching facts in API.md (Anthropic before Phase 1, OpenAI-chat before Phase 2, others before Phase 5).
- OQ-5: project name.
