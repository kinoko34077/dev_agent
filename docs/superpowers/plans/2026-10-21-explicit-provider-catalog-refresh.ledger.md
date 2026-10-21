# SDD ledger — plan: docs/superpowers/plans/2026-10-21-explicit-provider-catalog-refresh.md

Base: `bbe137a1782f82925109b6f514d9e16007a9ea90`

Task 1: implementation, focused verification, and local commit complete; PR/CI review pending — add explicit Provider selection to the existing candidate-only discovery CLI.

Pre-flight: one task; tests consume `select_bindings` and CLI selection, both produced by Task 1. No cross-task interface conflicts.

Ruling: use this tracked ledger beside the plan instead of `.superpowers/sdd/` — the prescribed workspace helper failed because its Bash path conversion tried to create `C:/Users/...`, and the sandbox denied creating the hidden scratch directory; a tracked ledger is recoverable with the implementation branch. Cost if wrong: this temporary planning record adds one documentation file to the PR.

RED/GREEN evidence: the initial focused tests failed because `select_bindings` and the CLI option were absent. After implementation, the five exact selector/CLI regressions passed (`5 passed, 28 deselected`, 4.09s) with pytest cache disabled. The initial pytest teardown hang was traced with `faulthandler` to `_pytest.cacheprovider` attempting `tempfile.mkdtemp()` in inaccessible Windows temp locations; setting `TEMP`/`TMP` to a dedicated temp directory under the writable primary workspace and disabling cacheprovider yielded a clean exit.

Read-only local discovery: invoked only `--provider ollama` against the existing bindings file and an explicit candidate output. It refreshed one exact binding and discovered four IDs (`gemma4:12b`, `nebel/fugaku-llm:13b-instruct`, `qwen3.5:9b`, `qwen:0.5b`). The candidate remains unmerged. The canonical diagnostic still reports stored catalog 1,385/current 0, and exact Qwen 9B current discovery/capability/billing admission remains unavailable; no model load/generation, admission change, or Gate change occurred.
