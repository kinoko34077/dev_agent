# Risk-Bounded Autonomous Development Architecture

**Status:** USER APPROVED — 2026-10-21; architecture and bounded implementation plan approved. Execution remains subject to the existing authority and Gate boundaries.
**Date:** 2026-10-21
**Target:** `kinoko34077/dev_agent`, maintained branch `v2/bootstrap`
**Conceptual direction:** Option A approved by the user: reuse the existing execution mechanisms and distinguish ordinary bounded operation from formal Phase 8 acceptance.

## 1. Purpose

Make dev_agent useful for safe, bounded development even when a formal acceptance lane or a particular external Provider is unavailable. Preserve real security, authority, cost, recovery, and formal acceptance boundaries. Do not equate "not formally accepted" with "all operation must stop," and do not equate "locally successful" with formal Phase 8 acceptance.

The target is a repeatable user-initiated development loop:

```text
read current purpose and durable work state
  -> choose an actually authorized, runnable work item
  -> acquire existing Claim when that work is governed by shared execution coordination
  -> plan and delegate through existing dev_agent development/runtime boundaries
  -> implement and Host-verify
  -> review and integrate through the existing authority
  -> record evidence and checkpoint on the owning Issue
  -> re-evaluate the larger objective and select the next safe action
```

This design does not assume that a ChatGPT/Codex turn runs forever, that a background worker is available, or that an empty/stale projection proves there is no work.

## 2. Current evidence and scope boundary

The following are observations from the 2026-10-21 review, not claims that the target operating loop is already complete:

- Local branch is `v2/bootstrap`, `HEAD=332a497277c452873e364af6024e6c9c1185c1e5`, worktree clean. GitHub comparison reports `v2/bootstrap` identical to that SHA.
- `devflow#15` was updated 2026-10-20. It identifies `dev_agent#65` as the formal Phase 8/R9 owner, `#11` as the protected formal authority, and `#81` as a separate provider-evidence support lane.
- `#65` remains `PRECHECK_BLOCKED / WO-1`; current formal supply and independent exact-route supply are recorded as zero, with no current exact `RUNTIME_ELIGIBLE` route and no fresh formal R9 root. Historical Provider failures/UNKNOWN identities are not retry authorization.
- `spec/v2/GATE_STATUS.json` keeps Phase 8 preparation at `PREPARATION_ONLY` and `LIVE_ACTIVATION=NOT_VERIFIED`. It records a completed local L1/non-Gate multi-role chain, which is useful operational evidence but does not satisfy the formal remote-provider acceptance condition.
- `devflow#107` still reports execution-coordinator as `BLOCKED`; its live Issue #3 snapshot has an empty current `claims` set. This does not establish that a particular candidate is claimable or that no candidate exists.
- `devflow#15`'s generated task projection is `INCOMPLETE` with unclassified open issues. A filtered or incomplete view must not be treated as proof that no work exists; the manual Control and owning Issues remain necessary.
- The local `docs/CURRENT_STATE.md` handoff table still names `4e59b83` / `18289041` as its accepted remote/implementation baselines, while live Control #15 and the matching local/GitHub branch identify `332a497`. Treat this as a stale projection to reconcile before operational execution; do not use the old table as the current branch pointer.
- A fresh Devflow bootstrap/work-view read on 2026-10-21 was available, but work-view coverage was `INCOMPLETE` (zero known TASKs, five unresolved open Issues, `can_authorize_execution=false`) and the explicit Codex Inbox call failed. Manual Control #15 and owning Issues remain necessary; neither partial visibility nor an empty Claim snapshot is proof of no work or execution authority.
- The current local model candidate diagnostic reports 0 current rows from 1,385 stored catalog rows; all 1,385 are stale/expired. The local Ollama inventory currently lists `qwen:0.5b` and `nebel/fugaku-llm:13b-instruct`, not the configured qwen3.5:9b primary route. No Worker route was selected or invoked for this first slice.

The checked repository docs and accepted plans describe existing Operation/RuntimeCoordinator, Commander/DevFarm/Supervisor, Host Verification, Review/Integration, Provider admission, Process Coordination, queue/state, and recovery boundaries. This design connects those owners; it does not replace them.

## 3. Architecture decision

Represent two **policy/acceptance scopes over the existing system**, not two runtimes:

| Scope | Purpose | Required route | Acceptance meaning |
| --- | --- | --- | --- |
| **Bounded Operations** | Ordinary low-risk, reversible repository work; read, analysis, planning, offline/local tests, and approved local-model execution | Any currently available, authorized, no-charge route; external generation is optional | Evidence for the specific task only. It does not promote a formal Gate. |
| **Formal Phase 8** | #65 single-root R9 / multi-role activation acceptance | Current exact-route admission and all formal qualification, billing, privacy, quota, liveness, role, verification, review, integration, continuation, and exact-head CI requirements | Only the existing #11 / Gate authority can accept or promote this lane. |

Both scopes use the same existing component owners, selecting only the paths applicable to a given task: Operation/runtime and queue/StateStore where applicable, Commander/DevFarm/Supervisor for development work, existing Host integration, Provider/admission, and execution-coordinator Claim authority where required. The scope selects policy and acceptance criteria; it must not create duplicate lifecycle, queue, scheduler, state store, approval authority, or integration path.

```text
Human purpose / constraints
          |
Active-turn upper commander (GPT/Codex)
  global priority, decomposition, replan; no assumed unattended liveness
          |
devflow Issues / Work Orders / Repository Control ---- KAEP handoff
          |                                               (message, not Claim)
          +---- existing execution-coordinator Claim/lease/fencing
          |     only for work governed by shared coordination
          |
dev_agent existing Operation + RuntimeCoordinator / Commander
          |                         + DevFarm/Supervisor
          +---- Provider admission or local/offline execution
          +---- Host Verification -> Reviewer -> deterministic integration
          |
          +---- owning Issue checkpoint -> global re-evaluation

SMC remains an existing low-priority maintenance source, not a new scheduler.
Bounded Operations and Formal Phase 8 are policy/acceptance scopes above this flow.
```

## 4. Responsibility and authority

| Owner | Owns | Must not become |
| --- | --- | --- |
| **devflow** | Cross-repository policy, Repository Control, Work Orders, KAEP conventions, shared operating rules | Detailed code truth, runtime queue, or claim ledger |
| **Upper commander (active GPT/Codex turn)** | Interpret the goal, compare priorities, decompose work, choose the next safe unit, replan after checkpoints | An always-on process, execution Claim authority, Host verifier, or merge authority outside existing rules |
| **execution-coordinator** | Serialized cross-repository Claim/lease/generation/fencing and its accepted runtime projections | Durable task/spec truth, a second task database, or proof that visible work is authorized |
| **dev_agent** | Its existing Operation/runtime, planning and Worker proposal paths, Host verification, Review/Integration boundaries and repository-local state | A second cross-repository Claim ledger or authority to waive formal Gates |
| **KAEP** | Bounded request/response/handoff and correlation | Claim, approval, execution, or durable task authority |
| **SMC** | Existing low-priority maintenance catalog/flow under its current rollout and publication rules | Synthetic work, a parallel scheduler, or a way to bypass no-fee / claim rules |
| **Human** | Purpose, constraints, explicitly Human-gated decisions and exceptional authority | Routine selection/retry/replanning for already-authorized bounded work |

Repository-local work ownership remains the owning Issue/Work Order plus existing Commander Plan/Supervisor path. A soft Execution Session Record is a recovery/checkpoint aid, not an atomic lock or substitute Claim. Shared cross-repository work uses the existing execution-coordinator Claim protocol when required by that work's accepted contract. A Claim is not supplied by Inbox visibility, an Issue title, or a generated projection.

## 5. Risk-bounded execution policy

Stop or defer only the affected action. Continue independent work when it has its own valid authority, scope, and safe execution path.

| Operation class | Default disposition | Conditions |
| --- | --- | --- |
| Read, inspect, compare, plan, offline analysis | Continue | Use current canonical sources; label uncertainty and stale/incomplete views. |
| Reversible repository edit, local tests, documentation | Continue in Bounded Operations | Existing task authority and non-overlapping scope; preserve branch/ownership; Host Verification and established review/integration rules still apply. |
| Local model / local test execution | Prefer when available | Stay inside demonstrated local capabilities and current resource/safety bounds; no formal L2 inference from local success. |
| External no-charge model/provider request | Only if current admission permits | Revalidate exact model/account/project, qualification, billing, privacy, quota, health and request semantics required by that route. Model listing or network reachability alone is insufficient. Use fresh request identity; never replay UNKNOWN/reconciliation-required effects. |
| Paid or potentially billable Provider action | Do not execute under this design | Existing no-paid-provider direction remains. A free-tier label does not prove zero charge or safe overage behavior. |
| Credential, session, permission, security boundary, approval authority, protected Gate change | Hold that action for its established Human/security authority | Continue unrelated safe work. Do not infer authorization from this design or an Issue/Inbox entry. |
| Destructive, irreversible, public-scope expansion, deployment, OS registration, resident service | Hold for the applicable explicit authority | Do not use a reversible-task permission as a proxy for deployment or publication permission. |
| UNKNOWN external effect or failed reconciliation | Quarantine that operation | No blind replay. Continue only independent work. |
| Genuine unresolved product/architecture choice with materially different outcomes | Ask the Human for that decision | Do not escalate mere technical uncertainty when a safe, evidence-based path is available. |

Host Verification is not an OS/network sandbox. Its actual isolation level and trust implications remain explicit; do not present unsandboxed execution as the highest-trust unattended mode.

## 6. Work selection and execution lifecycle

1. **Refresh canonical state.** Follow devflow `AGENTS.md` / #734, live Repository Control #15, target repository entry points and owning task. Use `bootstrap_repository`, work view, and explicitly Codex-filtered Inbox when available. On incomplete/unavailable reads, inspect the manual Control, owning Issue, devflow Control and #503 fallback. Never treat an empty filtered result as total absence.
2. **Build a bounded candidate set.** Only use actual Issues, Work Orders, accepted maintenance catalog items, or explicit user goals with an identifiable owner, expected revision, scope, acceptance, dependencies and recovery path. Do not manufacture idle tasks or infer assignment from a projection.
3. **Classify the operation.** Separate policy scope (Bounded Operations vs Formal Phase 8) from risk class, execution environment, cost, effect/reconciliation state, and authority. A blocked Provider route blocks only work requiring that route.
4. **Resolve ownership and collisions.** Reuse existing Commander Plan/Supervisor for repository-local development. For work under shared cross-repository coordination, validate the current candidate and acquire the existing serialized Claim before execution. If claimability or scope ownership is unknown, do not mutate that candidate; select another independently authorized task if available.
5. **Plan narrowly.** Assign non-overlapping ownership, concrete acceptance checks, excludes, and a bounded retry/recovery rule. Delegate only through existing Worker eligibility and manifest/content-scanning boundaries. Never send secrets, credentials, protected source or raw conversations externally.
6. **Execute on an available route.** Prefer local/offline/no-charge routes when valid. If an optional model/provider fails, preserve the task and use a materially different eligible route or perform independent offline work. Do not claim that a model ran if it did not.
7. **Verify at the Host boundary.** Run the checks relevant to the change; preserve syntax/tests/scope/secret/ownership/base-revision and other required checks. Review remains proposal-only; integration remains deterministic and uses the current accepted authority.
8. **Integrate and recover.** Integrate only a verified, reviewed candidate through the established path. Retain rollback/LKG and immutable attempt/effect evidence where applicable. Unknown external effects remain quarantined.
9. **Externalize one checkpoint.** Update the owning Issue/Work Order with exact base/head, completed acceptance, verification, current blocker, recovery path and next action. Update repository Current State only for an accepted repository-level state change; update devflow Control only for cross-repository state/entry-point changes.
10. **Globally replan.** Re-read the original objective and current canonical frontier. Choose CONTINUE, CHANGE_PATH, SPLIT, HOLD, or STOP. Do not repeat an accepted stage or the same blind failure. Continue to the next eligible unit in the same active turn where possible, but do not claim unattended continuity.

## 7. Failure containment and graceful degradation

- **Capability-specific:** missing Provider, tool, permission, or evidence disables only operations that require it. Offline work and independent tasks remain available.
- **Evidence-specific:** discovery/catalog presence, historical success, local L1 success, or model benchmarks do not satisfy current account-specific billing/quota/liveness/admission.
- **Retry-specific:** retry only with new information, a fresh identity where required, bounded attempts, and no unresolved external effect. Keep prior attempts immutable.
- **Authority-specific:** fail closed for the protected action, not the entire project. Do not auto-approve or let an Expert/Worker/Reviewer grant Human authority.
- **Integrity-specific:** fail closed for corruption, stale fencing, ambiguous ownership, or unreconciled effect; preserve last-known-good state and continue unrelated work.
- **Progress-specific:** if no valid candidate, Claim, route, or acceptance path exists, record the precise missing fact and the smallest recheck trigger. Do not manufacture a candidate merely to make the loop appear autonomous.

## 8. First operational vertical slice

The first demonstration is one small, reversible, non-secret, no-charge change selected from a real, current, authorized work item. It must traverse the actual operating path rather than a synthetic unit-test-only scenario:

```text
live source refresh
 -> valid work candidate and scope
 -> existing Claim acquisition where the candidate is Claim-governed
 -> plan/delegation through existing dev_agent boundaries
 -> real implementation proposal
 -> Host Verification
 -> independent review where required
 -> established integration path
 -> owning-Issue evidence/checkpoint
 -> release existing Claim where required
 -> global next-action re-evaluation
```

For the goal's requested Claim-to-integration proof, select a candidate actually governed by execution-coordinator Claim. Do not retrofit Claim onto unrelated local work merely to satisfy a diagram. If no such candidate is currently valid/claimable, keep the operational-cycle acceptance `NOT DEMONSTRATED`, record the exact source/eligibility blocker on the owning surface, and continue independent repository work without asserting the Claim criterion passed. The current empty Claim snapshot alone neither grants nor denies eligibility.

Acceptance for this slice:

- The candidate exists in its authoritative source and was not synthesized from idle state.
- Exact source revision, scope, owner, exclusions and acceptance are recorded before mutation.
- A Claim-governed candidate has a live successful Claim with matching task/role/generation; stale or merely visible Claim evidence is insufficient.
- The chosen execution route is demonstrably no-charge and within its admitted capability, or the task is completed without external generation.
- Worker/model output remains a proposal until Host Verification and required Review pass.
- Integration and continuation use existing paths; no parallel queue/runtime/state/authority is introduced.
- The owner Issue records the result, failed attempts, test evidence, rollback/recovery and next action.
- A final objective-level replan is recorded; formal Phase 8 remains unchanged.

## 9. Formal Phase 8 boundary

The Formal Phase 8 lane remains #65 execution + #11 Gate authority. It still requires the exact current admission and full single-root chain defined by the owning Work Order/Gate. Bounded Operations may produce useful code and operational evidence, but cannot:

- turn local L1 evidence into formal L2 evidence;
- create `RUNTIME_ELIGIBLE` from model names, catalog results, benchmark score, or network reachability;
- bypass qualification, billing, privacy, quota, health, role, Host Verification, Review, Integration, dependent continuation, or exact-head CI;
- change `PHASE8_PREPARATION`, `LIVE_ACTIVATION`, D9 production deployment, or any protected Gate.

Formal Gate decisions and the criteria for changing them remain with the existing protected authority. This document does not modify `spec/v2/GATE_STATUS.json`, #11, or #65 acceptance.

## 10. Availability and automation limits

The immediate design supports a durable, resumable **active-turn** workflow, not an unattended resident agent. Existing RuntimeCoordinator `serve` may be used where already authorized and operationally configured, but this design does not register startup, install a service, create schedules, create an automatic model heartbeat, or claim that a later ChatGPT task is continuation of the current session.

No new Scheduler, task queue, database, Claim ledger, Agent framework, approval store, or self-update authority is introduced. OS Guardian, Task Scheduler, reboot-owned recovery, paid Providers, new credentials/permissions, production deployment, and new public surfaces remain separately gated/deferred.

## 11. Rollout and acceptance boundaries

This is an architectural target, not the file-by-file implementation plan. After the user approves this written design, prepare a bounded plan that starts with existing-behavior inventory and the first real candidate/Claim path; implement only gaps proven to block the acceptance criteria.

The design is accepted for implementation only after explicit review of this document. The operating architecture is demonstrated only after:

1. A real limited-risk operation completes through selection, valid ownership/Claim where applicable, implementation, Host Verification, review/integration, durable checkpoint and global replan.
2. A Provider-unavailable or optional-capability failure is shown to block only the affected route while an independent safe task can continue.
3. No duplicate scheduler, queue, Claim authority, state store, approval authority, or lifecycle is introduced.
4. No paid Provider, credential/permission mutation, deployment, or protected Gate change occurs.
5. Formal Phase 8 status remains unchanged unless #11 accepts its own required evidence.
6. The owner issue and Current State contain truthful, revision-bound results and actionable resume conditions.

Repository tests and exact-head CI remain required at the release/integration boundary specified by repository policy; obvious already-passing checks are not to be repeated mechanically for every narrow step. Security/authority, migration, release, and exact-head evidence cannot be waived merely as "obvious tests."

## 12. Alternatives considered

- **A — Shared mechanisms, distinct operation/acceptance scopes (selected):** maximizes safe progress without conflating local operational evidence with formal acceptance; reuses all existing owners.
- **B — Require formal Phase 8 route for every code change:** rejected because provider admission is irrelevant to read/offline/local work and creates an artificial global stop.
- **C — Add a new autonomous scheduler/runtime to bypass current blockers:** rejected because it duplicates execution authority, does not solve admission or Claim truth, and would add unattended-operation risk.

## 13. Source and freshness note

Primary checked sources: devflow `AGENTS.md`, #734, #15, #503, #105, #107, #142, #175, #177, #190, #209, #233, #439, #541, #642, #760; dev_agent `AGENTS.md`, `docs/CURRENT_STATE.md`, `docs/SYSTEM_MAP.md`, `docs/V2_EXECUTION_PLAN.md`, `docs/V2_DETAILED_ROADMAP.md`, `spec/v2/GATE_STATUS.json`, #2, #11, #40, #64, #65, #81; execution-coordinator `AGENTS.md`, `project/docs/CURRENT_STATE.md`, `project/docs/INDEX.md`, #3; and the linked development principles, evidence, knowledge governance, request-mode/authority, and specification-management source documents.

Before any later slice, repeat the live source/authority refresh when freshness matters. The 2026-10-21 bootstrap succeeded but Inbox coverage did not; use manual Control and owning Issues as the fallback. Revalidate the current branch/HEAD, #15/#65/#11/#81 and any applicable execution-coordinator task/Claim evidence. This design remains an architectural decision record, not execution authority by itself.
