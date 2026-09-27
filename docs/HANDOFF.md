---
doc: HANDOFF
status: overwritten each session
last_verified_commit: none (pre-repo)
updated: 2026-09-27
---

# Handoff

**Current phase:** Phase 0 (Bootstrap). M0.1 and M0.2 merged. M0.3 (GitHub Actions CI) in review.

**Orchestration workflow:** Codex CLI (`codex exec`, medium reasoning) implements one milestone at a time on its own branch, with an explicit "implementation only" instruction — it does not run `git add`/`commit`/`push`, does not open the PR, and does not self-audit or declare a milestone done. Claude reads Codex's full transcript (not just its summary, which has previously claimed success on a no-op run), independently re-runs the gate commands, reviews the diff line by line, stages, commits, pushes, opens the PR, and posts the audit as a PR comment. The user merges (or Claude merges once told to).

**Next action:** Finish M0.3 audit and PR, then merge. After that, Phase 0 is complete; start Phase 1 with M1.0 (canonical request model + adapter contract).

**Blockers / pending decisions:**
- Branch protection on `main` not yet enabled (needs at least one CI run to exist first — M0.3 will provide it).
- Repo secrets for `integration.yml` (`PCG_ANTHROPIC_API_KEY`, `PCG_VECTOR_URL`, `PCG_VECTOR_API_KEY`) not yet set in GitHub; that workflow will fail if manually triggered until they are.
- ADR-008 embedding model: confirm local model (default proposal: fastembed bge-small, 384 dims); the Qdrant collection vector size depends on it.
- Qdrant Cloud: cluster URL and API key are in the local gitignored `.env`; connection not yet tested. The key was pasted into chat, so rotate it.
- Re-verify provider caching facts in API.md (Anthropic before Phase 1, OpenAI-chat before Phase 2, others before Phase 5).
- OQ-5: project name.
