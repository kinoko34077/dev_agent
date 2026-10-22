# SDD ledger — plan: docs/superpowers/plans/2026-10-21-host-dispatch-local-tier-forwarding.md

Base: `43a7d255309f6af87ce3ed5426c1657a3253d8a8` on `v2/bootstrap`.

Pre-flight: one task; the selected Host dispatch envelope supplies the child Ollama tier; no cross-task interface conflict.

Ruling: `sdd-workspace` helper could not create its workspace under this Windows sandbox because Git Bash passed the Windows repository path to `mkdir` as a drive-root path. A second attempt to create the ledger inside the managed worktree was denied by filesystem scope. I keep the plan-specific ledger in the writable primary checkout while source/test work remains in the attached worktree; no existing SDD workspace was changed. Cost if wrong: bookkeeping is split across the two checkouts, but the exact base and owning plan are recorded here.

Task 1: implementation and focused verification complete in both the isolated worktree and the primary checkout; integration/delivery pending.

RED: `python -m pytest -p no:cacheprovider tests/v2/test_host_provider_dispatch.py::test_host_process_executor_propagates_selected_ollama_tier_to_static_host_runtime -vv --tb=short` → expected `AssertionError: ['missing'] != ['L1']` (1 failed).

Runner diagnosis: ordinary pytest cacheprovider blocks during `pytest_sessionfinish` while creating its temporary cache support files in this sandbox. Disabling only `cacheprovider` allows a passing existing test to exit normally; this is a test-runner environment constraint, not a production-code change.

Ruling: do not perform a fresh local generation attempt. Read-only admission with `OLLAMA_INTELLIGENCE_TIER=L1` still admitted 0 candidates, and the current trusted billing profiles for qwen3.5:9b, gemma4:12b, and qwen3.5:4b all resolve as not current (their recorded review window ended 2026-10-09). Billing catalog is budget authority; do not refresh it in this implementation slice. Cost if wrong: local work waits despite zero direct model charge; this avoids overrunning stale cost evidence.

Finding: selected-tier forwarding is a real separate Host boundary defect for a current local profile, but it does not resolve today's expired-profile blocker. Keep the minimal fix/test, verify locally, and report the operational cycle as blocked pending authorized billing-profile refresh.

GREEN / review: `python -m pytest -p no:cacheprovider tests/v2/test_host_provider_dispatch.py -q` → `22 passed in 6.45s`; `git diff --check` clean. Self-review: tier forwarding is inside the Ollama-only branch, uses the validated Host-selected envelope tier, does not alter non-Ollama child environments, and does not relax admission or safety checks. No secrets or authority files changed.

Sync attempt: live GitHub branch readback and both local checkout HEADs resolve to `43a7d255309f6af87ce3ed5426c1657a3253d8a8`. Remote currently matches the task base. The original source/test diff remains preserved in the attached isolated worktree; the same two-file patch was applied to the writable primary checkout for repository delivery. Primary checkout's pre-existing untracked SDD artifacts remain untouched.

Fresh primary-checkout verification: `python -m pytest -p no:cacheprovider tests/v2/test_host_provider_dispatch.py -q` → `22 passed in 5.87s`; `git diff --check` exit 0 (only Git LF→CRLF notices). The local Ollama inventory contains `qwen3.5:9b` and `gemma4:12b`; `ollama ps` reports no loaded models. RuntimeCoordinator read-only health is `DEGRADED`, with a stale peer lease (`heartbeat_at` 2026-09-24); the referenced PID is not present. No runtime service was started. Exact local L1 no-charge admission remains 0 because the reviewed profile is expired; no model request/generation was attempted.

Delivery: implementation commit `8cb6a264d3c0301bdac3b3b585c11995335cd288` was pushed fast-forward to `origin/v2/bootstrap`. Full local regression: `1959 passed, 1 skipped in 741.00s`; Architecture `ARCHITECTURE_PASS`; changed-file `py_compile` PASS. Exact implementation-head Actions: v2-core run `39590155266` (kernel 3.10/3.11) and provider-smoke run `39590155176`: all SUCCESS. Current State synced in docs-only commit `edf1df046efc9ffc4c566f4f790eb75bde80fb3c`, also pushed fast-forward; exact docs-head runs `39590501281` and `39590501246`: all three checks SUCCESS.

The current branch tip is the docs-only `edf1df0`; implementation baseline is `8cb6a26`. Formal #65 remains PRECHECK_BLOCKED, #11 unchanged. The issue-comment write is still unavailable: Devflow reads fail and the only exposed GitHub comment mutation is PR-scoped; no alternate write surface was used. The next technical finding is that a zero-candidate Host child admission currently becomes `host_configuration`, obscuring a confirmed pre-provider no-route/admission rejection. Keep this as a separate narrowly tested slice; do not relax admission or billing evidence.

Issue-first reporting blocker: Devflow bootstrap, Repository Control, work view, and Codex Inbox calls returned tool errors. Manual read-only fallback confirmed dev_agent #97 is the owning bounded-work issue, devflow #15 is the repository Control, and #503 is the KAEP fallback. The available GitHub comment tool is explicitly PR-only and its #97 write attempt was rejected as target/authority mismatch; do not retry through another write surface. Preserve details here pending a valid issue-comment path.
