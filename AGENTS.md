# dev_agent v2 Codex entrypoint

This repository is maintained on `v2/bootstrap`. `main` is the frozen v1
line and is not a v2 implementation baseline.

## Read order

1. `docs/CURRENT_STATE.md` — current implementation and evidence
2. `docs/SYSTEM_MAP.md` — ownership and dependency map
3. `docs/V2_EXECUTION_PLAN.md` — phase order and stop conditions
4. `docs/DEVFARM.md` — development Worker boundary
5. `docs/CODEX_COMMANDER.md` — parent-plan operation procedure
6. `spec/v2/` and the relevant requirements chapter — exact contract

Confirm the branch, `HEAD`, worktree status, and current Gate status before
changing code. Preserve existing Controller, Queue, StateStore, Provider,
Resource, Recovery, authority, lease/fencing, effect-intent, and reconciliation
boundaries. Do not add a parallel production scheduler or Agent framework.

## Commander and Worker rules

- Codex owns decomposition, dependency DAG, ownership, review, integration,
  conflict resolution, full regression, and evidence/doc synchronization.
- A Free Worker receives one narrow manifest and writes only its isolated
  worktree or proposal artifact. Workers never communicate directly.
- Readable files and externally outbound files are different scopes.
- Every proposal must pass deterministic patch validation and Host Verification
  before it is treated as a candidate for integration.
- `scripts/devfarm_commander.py` stores development-only parent plans under
  `.devfarm/plans/`; it composes the existing DevFarm and never edits the
  official branch automatically.
- Do not assign overlapping file ownership. Keep Remote proposal concurrency
  separate from the small Host verification budget.
- Use explicit bounded retry/reassign. Never replay an unknown external effect
  or bypass approval, budget, privacy, Recovery, or Gate authority.

Protected areas include `spec/v2/GATE_STATUS.json`, budget authority,
`recovery/`, credentials/secrets/private keys, `.env*`, `.git/`, and
`.devfarm/`. Only a human-approved protected path operation may change those.

## Delivery

Run focused tests, the full `tests/v2` regression, and relevant read-only Gate
checks. Keep live Provider and external blockers truthful. Update the owning
Current State/traceability document when implementation evidence changes.
When the scoped slice is verified, commit and push it to `origin/v2/bootstrap`;
do not rewrite history or promote a Gate from an artifact alone.
