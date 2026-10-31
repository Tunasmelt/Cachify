---
doc: HANDOFF
status: overwritten each session
last_verified_commit: none (pre-repo)
updated: 2026-10-24
---

# Handoff

**Current phase:** Phase 2 in progress. M2.1, M2.2 and M2.3 merged (diff excerpts deferred to M3.2). M2.4 (openai-chat adapter) is next and planned. Gates G0 (backfilled review) and G1 (conditional pass, live check pending) are recorded in `reviews/`.

**Orchestration workflow:** Codex CLI (`codex exec`, medium reasoning) implements one milestone at a time on its own branch, with an explicit "implementation only" instruction — it does not run `git add`/`commit`/`push`, does not open the PR, and does not self-audit or declare a milestone done. Claude reads Codex's full transcript (not just its summary, which has previously claimed success on a no-op run), independently re-runs the gate commands, reviews the diff line by line, stages, commits, pushes, opens the PR, watches CI, and posts the audit as a PR comment. The user merges (or Claude merges once told to). Dependabot PRs get the same treatment (audited, CI checked, merged) rather than auto-merge.

**Branch protection on `main`:** PR required (0 approvals needed — solo repo, GitHub won't self-approve), status checks `ci`/`gitleaks`/`pip-audit` required and must be up to date with base, `enforce_admins` on, no force-push, no deletion.

**Next action:** M2.4 (openai-chat adapter), then the G2 review. Open decisions for M2.4: store input tokens as uncached (recommended), do not inject `stream_options.include_usage`, placeholder `default_ttl_s` of 300 s marked unverified. Still open: `--anthropic-live` test and repo secrets to close G1.

**Blockers / pending decisions:**
- Repo secrets for `integration.yml` (`PCG_ANTHROPIC_API_KEY`, `PCG_VECTOR_URL`, `PCG_VECTOR_API_KEY`) not yet set in GitHub; that workflow will fail if manually triggered until they are.
- ADR-008 embedding model: confirm local model (default proposal: fastembed bge-small, 384 dims); the Qdrant collection vector size depends on it. Matters from Phase 4.
- Qdrant Cloud: cluster URL and API key are in the local gitignored `.env`; connection not yet tested. The key was pasted into chat, so rotate it.
- Re-verify provider caching facts in API.md before each provider's phase (Anthropic before Phase 1, OpenAI-chat before Phase 2, others before Phase 5).
- `dependabot.yml` uses `package-ecosystem: "pip"`; GitHub's `"uv"` ecosystem may be more precise, unverified — low priority.
- OQ-5: project name.
