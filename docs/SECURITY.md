---
doc: SECURITY
status: draft
last_verified_commit: none (pre-repo)
updated: 2026-09-25
---

# Security

Plain version: this gateway sees every prompt, every answer, and every API key that passes through it. The main jobs are: don't store keys, don't store more prompt content than asked, and never hand one person's cached answer to someone else.

## Threat model by component

| Component | Threat | Guardrail |
|---|---|---|
| Gateway ingress | Unauthenticated use of admin routes | `PCG_ADMIN_TOKEN` required on `/pcg/*`; constant-time compare. |
| Gateway ingress | Abuse as open proxy | Bind to localhost by default; upstream allowlist; optional client token. |
| Provider adapter | API key leakage | Keys forwarded in memory only; stored as salted hash (`tenant_hash`); log redaction filter for `x-api-key` / `authorization`. |
| Provider adapter | SSRF via configurable upstream | Upstream URLs from env only (`PCG_UPSTREAMS`), validated against allowlist at startup; clients select by name only; unknown names rejected. |
| Provider adapter | Key leakage across auth styles | Redaction filter covers `x-api-key`, `authorization`, `x-goog-api-key` and any `key=` query param; per-upstream key-leak grep test. |
| Stream tee | Memory blowup on huge streams | Bounded internal buffer; drop analysis (not the client stream) if exceeded. |
| Store / bodies | Sensitive prompt data at rest | Log mode default `hash`; `full` is opt-in; retention job deletes bodies after `PCG_BODY_RETENTION_H`. |
| Diff engine | Leaks content via diff excerpts | Excerpts obey log mode; truncated length cap. |
| Semantic cache | Cross-tenant / cross-context answer leakage | Scope by tenant_hash + fingerprint (ADR-005); tests in G4. |
| Semantic cache | Cache poisoning (attacker seeds a bad answer that later matches others) | Tenant scoping; eligibility policy; high default threshold; invalidation API; optional "only cache from trusted sessions" flag. |
| Semantic cache | Stale or harmful answers served | Mandatory expiry; tag invalidation; hit logging with source_request_id for traceability. |
| Vector backend | Credential leak | Keys from env; never logged; least-privilege API key if backend supports it. |
| Dashboard | XSS via rendered prompt content | Render as text, never HTML; CSP headers. |
| Dependencies | Supply-chain | Lockfile, `pip-audit`, pinned embedding model revision, Dependabot. |
| CI/CD | Secret exfiltration via workflows or fork PRs | Secrets only in GitHub Actions secrets; `ci.yml` needs none; integration workflow is manual and never runs on fork PRs; workflow permissions default read-only; third-party actions pinned by SHA. |
| Repo | Unreviewed or unscanned changes reach `main` | Branch protection: PR + required checks (ci incl. gitleaks) + Claude audit review; no force push. |

## Guardrails (always on)
- G-1 Fail open for traffic, fail closed for admin access.
- G-2 No plaintext provider keys anywhere on disk.
- G-3 No semantic hit without matching tenant + fingerprint.
- G-4 No cached response containing tool_use.
- G-5 Every cache entry and stored body has an expiry.

## Per-gate checklist
- [ ] Secret scan clean (gitleaks) on the phase diff (enforced in CI on every PR).
- [ ] Workflow changes: read-only default permissions, actions pinned, no new secrets exposed to PR builds.
- [ ] Grep test: no provider key substrings in DB, logs, or fixtures (for every registered upstream and auth style).
- [ ] Semantic hits never cross wire formats or upstreams.
- [ ] New endpoints listed in API.md with auth requirement.
- [ ] Log mode respected by any new code that touches prompt/response content.
- [ ] New stored fields have retention/expiry defined.
- [ ] `pip-audit` clean (or exceptions recorded in DECISIONS.md).
