---
doc: PHASES
status: draft
last_verified_commit: none (pre-repo)
updated: 2026-09-25
---

# Phases → milestones → gates

Every gate passes only when its commands exit 0 and its security checks are ticked. On pass, write `reviews/phase-N-gate.md`.

Standard gate commands (all phases):
```
ruff check . && ruff format --check .
mypy src/
pytest -q
```

## Delivery workflow (ADR-014)
- One milestone = one branch (`phase-N/mN.M-slug`) = one PR into `main`. No direct pushes to `main`.
- Codex implements on the branch and opens the PR. PR body: milestone ID, REQ-IDs covered, tests added, security checklist items touched.
- Claude audits before merge: runs the gate commands locally, reviews the diff against the docs and SECURITY.md, and records the result as a PR review (approve, or request changes and send back to Codex).
- Merge only when CI is green and the audit passes (squash merge). Update HANDOFF/CHANGELOG (and DECISIONS if a decision changed) in the same PR.
- Final PR of each phase adds `reviews/phase-N-gate.md`.

## GitHub Actions
| Workflow | Trigger | Jobs |
|---|---|---|
| `ci.yml` | PR + push to `main` | lint (ruff), types (mypy), tests (pytest, marker-selected per phase, Python matrix as needed), gitleaks secret scan, `pip-audit`. Uses no cloud secrets. |
| `integration.yml` | manual `workflow_dispatch` (and nightly optional) | `pytest -m integration` against live providers / Qdrant Cloud using repo secrets. Never runs on fork PRs. |
| `dashboard.yml` (Phase 5) | PR touching `dashboard/` | `pnpm build && pnpm test`. |
| `release.yml` (Phase 6) | version tag `v*` | build Docker image, scan it, publish to registry (chosen at M6.3), run migration check. |

Repo settings: branch protection on `main` (PR required, required checks: ci, no force push), Dependabot for pip/actions/npm, secrets stored only in GitHub Actions secrets.

## Phase 0: Bootstrap
Milestones
- M0.1 Repo skeleton: `src/pcg/`, `tests/`, `docs/`, pyproject (uv), pre-commit.
- M0.2 Config loader (pydantic-settings) with all env vars from ARCHITECTURE.md.
- M0.3 GitHub Actions `ci.yml` (standard gate commands, gitleaks, pip-audit), PR template, CODEOWNERS, Dependabot config, branch protection on `main`, `integration.yml` (manual) skeleton.

Gate G0
- Commands: standard + `python -c "import pcg"`
- Tests: CI runs green on a PR; a deliberately failing lint/test blocks merge (verified once).
- Covers: none (infra)
- Security: `.env` in .gitignore; secret scanning in pre-commit and CI (gitleaks); no secrets required by `ci.yml`.

## Phase 1: Passthrough proxy
Milestones
- M1.0 Canonical request model, `ProviderAdapter` contract, `CacheProfile`/`Usage` types, upstream registry stub, adapter conformance suite (ADR-011..013).
- M1.1 `POST /v1/messages` non-streaming passthrough via the Anthropic adapter.
- M1.2 SSE streaming passthrough with Stream Tee.
- M1.3 Usage extraction (input, cache write, cache read, output) and `requests` row written in background.
- M1.4 Fail-open wrapper.

Gate G1
- Commands: standard + `pytest -m proxy` + `pytest -m integration --anthropic-live` (manual, needs key)
- Tests: recorded fixtures for streamed and non-streamed responses; client receives byte-identical stream; upstream 4xx/5xx passed through unchanged; forced internal exception still returns upstream response.
- Tests (added): anthropic adapter passes the conformance suite; null vs 0 usage handling; upstream selection only from the registry.
- Covers: REQ-GW-01 (anthropic), 02, 03, 06, 07, 08, REQ-BD-05, REQ-OBS-01 (partial)
- Security: API key never appears in DB or logs (test greps DB + log output); upstream allowlist enforced; unknown upstream name rejected.

## Phase 2: Segmentation, lineage, diff
Milestones
- M2.1 Segmenter + dual hasher.
- M2.2 Lineage via session header, then prefix inference.
- M2.3 First-divergence diff engine.
- M2.4 openai-chat adapter (proves the abstraction on a non-breakpoint format; covers OpenAI-compatible providers).

Gate G2
- Commands: standard + `pytest -m detector`
- Tests: golden request pairs (identical, tool reorder, timestamp in system, edited earlier message, key-order-only change) produce the expected divergence index and raw/canon result, for both anthropic and openai-chat fixtures; openai-chat adapter passes the conformance suite.
- Covers: REQ-BD-01, 02, 03, 04, REQ-GW-01 (openai-chat)
- Security: diff excerpts respect `PCG_LOG_MODE` (no content stored when `hash`/`off`).

## Phase 3: Classification + CLI
Milestones
- M3.1 Classifier with all seven classes (incl. NOT_REPORTED), rules selected by `cache_mode`.
- M3.2 Diagnosis rule set.
- M3.3 `pcg report` and `pcg busts` CLI.

Gate G3
- Commands: standard + `pytest -m classify` + `pcg report --fixture tests/traces/basic.jsonl`
- Tests: each class and each diagnosis has at least one fixture trace, covering EXPLICIT_BREAKPOINT, AUTO_PREFIX and NONE cache modes; UNEXPLAINED rate on the fixture corpus is 0.
- Covers: REQ-BD-06, 07, 08, REQ-OBS-02 (partial)
- Security: CLI reads DB read-only.

## Phase 4: Semantic cache
Milestones
- M4.1 Embedder interface + local model.
- M4.2 VectorStore interface + Qdrant (local mode) + in-memory backend for tests.
- M4.3 Fingerprinting, eligibility policy, threshold.
- M4.4 Response replay (JSON + synthesized SSE).
- M4.5 Expiry, cleanup job, invalidation admin API.

Gate G4
- Commands: standard + `pytest -m semantic`
- Tests: same question + different system prompt → miss; paraphrase above threshold → hit; tool calls never cached (`tool_use` / `tool_calls`); expired entry never served; same question via a different wire format or upstream → miss; replayed SSE parses in the official Anthropic and OpenAI SDKs.
- Covers: REQ-SC-01..08, REQ-GW-05, REQ-SEC-04
- Security: cross-tenant lookup test (tenant A cannot hit tenant B entry); invalidation endpoint requires admin token.

## Phase 5: Observability + OpenAI
Milestones
- M5.1 Admin API for requests, busts, metrics (OpenAPI generated → API.md).
- M5.2 Next.js dashboard: hit ratios, savings, bust timeline with diffs.
- M5.3 openai-responses adapter.
- M5.4 gemini native adapter (implicit caching usage field; conformance suite required).

Gate G5
- Commands: standard + `pytest -m admin` + `pnpm -C dashboard build && pnpm -C dashboard test`
- Covers: REQ-OBS-01..03, REQ-SEC-01, REQ-GW-01 (openai-responses, gemini)
- Security: dashboard behind admin token; no bodies rendered when log mode is `hash`/`off`.

## Phase 6: Bust trace corpus, hardening, deploy
Milestones
- M6.1 Corpus of deliberately cache-busting agent traces (QA-style regression suite).
- M6.2 Latency benchmark for REQ-GW-04.
- M6.3 Docker image, Postgres migration, hosted vector backend, `release.yml` deploy workflow (registry/host chosen here, recorded as an ADR).
- M6.4 Write-up with real savings numbers from running against own agents.

Gate G6
- Commands: standard + `pytest -m corpus` + `python bench/latency.py --assert-p95-ms 15`
- Covers: REQ-GW-04, REQ-SEC-02, 03 (final verification)
- Security: SECURITY.md per-gate checklist fully ticked; retention job verified; dependency audit (`pip-audit`) clean.
