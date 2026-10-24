---
doc: phase-1-gate
status: conditional pass (live check deferred)
commit: f2db082
date: 2026-10-24
---

# Gate G1: Passthrough proxy

Result: **conditional pass.** All offline gate checks pass. The live Anthropic check (`pytest -m integration --anthropic-live`) is deferred, pending repo secrets and a key.

## Commands (run on `f2db082`)
| Command | Result |
|---|---|
| `ruff check .` | pass |
| `ruff format --check .` | pass (41 files) |
| `mypy src/` | pass (12 source files) |
| `pytest -q` | pass (88 tests) |
| `pytest -m proxy` | pass (77 passed, 11 deselected) |
| `pytest -m integration --anthropic-live` | **deferred** (manual, needs `PCG_ANTHROPIC_API_KEY`) |

## Test requirements
| Requirement | Evidence |
|---|---|
| Recorded fixtures, streamed and non-streamed | `tests/fixtures/anthropic/` (plain, tool_use, truncated SSE; plain/tool_use/error JSON) |
| Client receives byte-identical stream | `test_stream_is_byte_identical_and_callback_gets_summary` (test_stream_tee.py), `test_gateway_streaming.py` |
| Upstream 4xx/5xx passed through unchanged | `test_upstream_errors_are_passed_through` (429, 500); `test_streaming_request_passes_through_upstream_error_without_callback` |
| Forced internal exception still returns upstream response | `test_failopen.py`, `test_gateway_records.py` (insert and migration failure) |
| Anthropic adapter passes conformance suite | `test_anthropic_adapter.py` via `assert_adapter_conforms`; fake adapter also checked |
| null vs 0 usage | `test_missing_required_start_usage_does_not_invent_zeroes`, `test_build_record_without_usage_keeps_tokens_null`, `test_non_stream_error_records_null_usage` |
| Upstream selection only from registry | `test_unknown_upstream_is_rejected`, `test_conflicting_upstream_selection_is_rejected`, `test_upstreams.py` |

## Security
- [x] API key never appears in DB or logs: `test_non_stream_usage_is_recorded_and_key_is_absent` (greps DB file bytes and log records), `test_key_is_absent_from_logs_response_and_settings_repr`, `test_streaming_api_key_is_absent_from_logs`, `test_exception_message_never_leaks_key_to_logs_or_response`.
- [x] Tenant identity stored only as HMAC hash (ADR-015).
- [x] Upstream allowlist enforced; unknown upstream name rejected.
- [x] Only the allowed client auth header is forwarded (`test_only_allowed_client_auth_header_is_forwarded`).

## Covers
REQ-GW-01 (anthropic), 02, 03, 06, 07, 08, REQ-BD-05, REQ-OBS-01 (partial), per PHASES.md. Not re-derived against PRODUCT.md in this review.

## Open items
- **Live check deferred.** No `integration`-marked test exists yet; `conftest.py` has the `--anthropic-live` skip hook only. Write the live test and set repo secrets before treating G1 as fully closed.
- Security greps cover the one Anthropic auth style; re-check when more upstreams or auth styles are registered.
- Local `.venv` is broken (leftover pytest temp dir); gates were run from a separate uv environment.
