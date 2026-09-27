## Milestone

<!-- Example: M0.3 -->

## What

<!-- Briefly describe the change. -->

## Tests

<!-- List tests added and commands run. -->

## REQ-IDs

<!-- List covered requirement IDs, or "None (infra)". -->

## Security checklist

- [ ] Secret scan clean (gitleaks) on the phase diff (enforced in CI on every PR).
- [ ] Workflow changes: read-only default permissions, actions pinned, no new secrets exposed to PR builds.
- [ ] Grep test: no provider key substrings in DB, logs, or fixtures (for every registered upstream and auth style).
- [ ] Semantic hits never cross wire formats or upstreams.
- [ ] New endpoints listed in API.md with auth requirement.
- [ ] Log mode respected by any new code that touches prompt/response content.
- [ ] New stored fields have retention/expiry defined.
- [ ] `pip-audit` clean (or exceptions recorded in DECISIONS.md).

## Notes

<!-- Optional reviewer context or follow-up work. -->
