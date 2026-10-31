---
doc: phase-0-gate
status: pass (backfilled)
commit: 73f991b (main) plus msgpack lockfile bump
date: 2026-11-01
---

# Gate G0: Bootstrap

Result: **pass.** G0 was declared passed on 2026-09-27 (HANDOFF and CHANGELOG) without a review file. This file was backfilled on 2026-11-01 by re-checking every G0 requirement against the current repo and GitHub. One requirement had no recorded evidence at the time and was verified retroactively; see below.

## Commands
| Command | Result |
|---|---|
| `ruff check .` | pass |
| `ruff format --check .` | pass (52 files) |
| `mypy src/` | pass (16 source files) |
| `pytest -q` | pass (138 tests) |
| `python -c "import pcg"` | pass (`pcg.__version__` is `0.0.0`) |

Run in a fresh `uv sync` environment against `main` plus the `msgpack` lockfile bump (#20), which was needed because `pip-audit` began failing on `main` on 2026-10-31.

## Tests
| Requirement | Evidence |
|---|---|
| CI runs green on a PR | M0.3 (PR #4) ran `ci`, `gitleaks` and `pip-audit` green at 2026-09-27 18:49:56Z and merged at 18:50:46Z; every later PR (#7 to #18) went through the same checks. |
| A deliberately failing lint/test blocks merge | **Verified on 2026-11-01** with throwaway PR #19 (closed unmerged, branch deleted): a test containing `assert False` made `ci` fail, and GitHub reported `mergeStateStatus: BLOCKED` with the PR non-draft and otherwise mergeable. Earlier, PR #4's first run also failed `gitleaks` (an action breaking change) and was fixed before merge. The gate originally said "verified once" but nothing recorded it; this is that record. |

## Security
- [x] `.env` is in `.gitignore` (`.env`, `.env.*`, with `!.env.example`); `git check-ignore` confirms it, and the only tracked env file is `.env.example`.
- [x] Secret scanning in pre-commit (gitleaks v8.30.0) and in CI (`gitleaks.yml`, required check).
- [x] No secrets required by `ci.yml` (it references none). `integration.yml` is `workflow_dispatch` only, so it never runs on pull requests.

## Repository settings (checked via the GitHub API on 2026-11-01)
- `main`: PR required, required checks `ci`, `gitleaks`, `pip-audit`, branches must be up to date, admins enforced, force-push and deletion disabled.
- `CODEOWNERS`, PR template and Dependabot (pip, github-actions) are present.

## Covers
None (infra), per PHASES.md.

## Open items
- `dependabot.yml` uses `package-ecosystem: "pip"`; GitHub's `"uv"` ecosystem may suit the `uv.lock` workflow better (unverified, low priority).
- Review files are meant to be written when the gate passes. G0 and G1 were both late; future gates should write the file in the final PR of the phase.
