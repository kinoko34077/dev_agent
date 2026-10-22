### Task 5: Acceptance evidence, Current State reconciliation, and global replan

**Paths:** owner Issue from Task 2; `docs/CURRENT_STATE.md`; `spec/v2/GATE_STATUS.json` (read-only); devflow #15 only if the cross-repository active-work projection genuinely changes.

- Persist bounded evidence in the owner Issue: exact base/integration revision, scope, worker route or delegation reason, Claim applicability/result, verification, review, integration, tests, recovery, and blockers. Exclude raw output and secrets.
- Reconcile Current State: distinguish accepted implementation baseline from final docs/evidence revision; avoid a self-referential commit claim and retain accurate historical chronology.
- Assert formal boundary: verify `spec/v2/GATE_STATUS.json` is unchanged, #65 remains `PRECHECK_BLOCKED` absent new accepted evidence, and #11 authority is untouched.
- Publish and verify exact revision after review and scoped checks; create a new non-rewritten commit/PR and exact-head CI. If the branch advances, stop and reconcile; never force.
- Re-evaluate the larger objective. Resume #65 only on a new safe admission signal; otherwise choose an actual independent Bounded Operation. Keep Claim-to-integration proof `NOT DEMONSTRATED` if no governed candidate was available.

**Verification:** documentation-only: `git diff --check` plus Markdown link review. If source changes occur, run changed-scope focused tests; do not repeat passing checks without a relevant diff or failure.
