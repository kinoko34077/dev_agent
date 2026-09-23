# SDD ledger — plan: docs/superpowers/plans/2026-09-24-discord-human-runtime-hardening.md

Pre-flight: tasks share the existing Discord outbound ConversationLog and Core Operation boundaries; Task 1 produces the final response projection contract consumed by Task 4, Task 2 produces reply metadata consumed by Task 4, and Task 3 consumes the existing Operation/Queue maintenance path without introducing a new scheduler.

Ruling: execute in the current `v2/bootstrap` checkout because the user explicitly requested implementation and push on that branch; no main/master branch is involved. Cost if wrong: concurrent edits would need reconciliation.

Task 1: complete — production `task.completed` payload shape is now covered by `test_final_response_accepts_production_task_completed_text_payload`; focused outbound suite passed 8/8.

Task 2: complete — numeric Discord reply references now survive inbound/history conversion, and HumanResponse acknowledgements are recorded as outbound conversation rows; Discord bot/history/outbound focused suites passed 29/29.

Task 3: complete — explicit WAIT on an active binding now returns bounded `WAIT_DEFERRED` instead of silently becoming NOTE, and conversation archive maintenance is injected into the existing outbound projection pass with failure isolation; composition/wait/archive/operation/outbound focused suites passed 80/80.

Ruling: do not pause a leased active Worker from Discord ingress; return explicit `WAIT_DEFERRED` until an existing cooperative Core checkpoint contract can own parent park/resume. Cost if wrong: active-run phrases requesting a timed pause still require a future Core checkpoint implementation, but no lease theft or false timer is introduced.

Task 4: implementation verification complete — focused Discord suite 66/66, full `tests/v2` 1615 passed/1 skipped, Architecture PASS, compileall PASS, evidence JSON valid, diff check clean, and tracked-file credential scan found no Discord bot token. Exact-head CI is pending the required push.
