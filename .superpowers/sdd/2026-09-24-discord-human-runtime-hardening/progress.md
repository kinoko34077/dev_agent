# SDD ledger — plan: docs/superpowers/plans/2026-09-24-discord-human-runtime-hardening.md

Pre-flight: tasks share the existing Discord outbound ConversationLog and Core Operation boundaries; Task 1 produces the final response projection contract consumed by Task 4, Task 2 produces reply metadata consumed by Task 4, and Task 3 consumes the existing Operation/Queue maintenance path without introducing a new scheduler.

Ruling: execute in the current `v2/bootstrap` checkout because the user explicitly requested implementation and push on that branch; no main/master branch is involved. Cost if wrong: concurrent edits would need reconciliation.

Task 1: complete — production `task.completed` payload shape is now covered by `test_final_response_accepts_production_task_completed_text_payload`; focused outbound suite passed 8/8.
