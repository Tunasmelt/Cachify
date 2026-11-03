---
doc: HANDOFF
status: overwritten each session
last_verified_commit: a4a0cda
updated: 2026-11-03
---

# Handoff

**Current phase:** Phase 2 complete (G2 conditional pass, `reviews/phase-2-gate.md`). Phase 3 (classification + CLI) is next. Gates G0 (backfilled) and G1 (conditional, live check pending) are in `reviews/`.

**Orchestration workflow:** Codex CLI (`codex exec`, medium reasoning) implements one milestone at a time on its own branch, with an explicit "implementation only" instruction — it does not run `git add`/`commit`/`push`, does not open the PR, and does not self-audit or declare a milestone done. Claude reads Codex's full transcript (not just its summary, which has previously claimed success on a no-op run), independently re-runs the gate commands, reviews the diff line by line, stages, commits, pushes, opens the PR, watches CI, and posts the audit as a PR comment. The user merges (or Claude merges once told to). Dependabot PRs get the same treatment (audited, CI checked, merged) rather than auto-merge.

**Branch protection on `main`:** PR required (0 approvals needed — solo repo, GitHub won't self-approve), status checks `ci`/`gitleaks`/`pip-audit` required and must be up to date with base, `enforce_admins` on, no force-push, no deletion.

**Next action:** Phase 3, M3.2 (diagnosis rule set, plus the readable diff excerpt per ADR-018: `full`-mode only, in-memory LRU into `bust_events`). M3.1 (classifier) is done: see ARCHITECTURE.md section 7 for the rules and the approximations M3.2 may need to refine (`truncated` divergence treated as an intact prefix; AUTO_PREFIX prefix size estimated from bytes). Then M3.3 (CLI, including `pcg serve`). To close G1: set the `PCG_ANTHROPIC_API_KEY` repo secret and run `integration.yml`.

**Blockers / pending decisions:**
- Repo secrets for `integration.yml` (`PCG_ANTHROPIC_API_KEY`, `PCG_VECTOR_URL`, `PCG_VECTOR_API_KEY`) not yet set in GitHub; that workflow will fail if manually triggered until they are.
- ADR-008 embedding model: confirm local model (default proposal: fastembed bge-small, 384 dims); the Qdrant collection vector size depends on it. Matters from Phase 4.
- Qdrant Cloud: cluster URL and API key are in the local gitignored `.env`; connection not yet tested. The key that was pasted into chat has been rotated (2026-11-03); put the new one only in the local `.env`.
- Re-verify provider caching facts in API.md before each provider's phase (Anthropic before Phase 1, OpenAI-chat before Phase 2, others before Phase 5).
- `dependabot.yml` uses `package-ecosystem: "pip"`; GitHub's `"uv"` ecosystem may be more precise, unverified — low priority.
- OQ-5: project name (see PRODUCT.md for the current naming).
- `PCG_LOG_MODE=off` still stores hashes and lineage; confirm that is the intent (G2 open item).
