# Codex-Supervised DevFarm Dogfood Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a durable, low-context Codex Supervisor projection that composes existing Commander / DevFarm primitives, exposes a single changeable heartbeat cadence, and supports safe external Payload references.

**Architecture:** Store optional, typed Supervisor metadata in the existing Commander Plan so the existing JSON persistence and optimistic CAS remain the sole development-run storage. A development-only `CodexSupervisedCommanderRun` facade reads that metadata and calls existing `dispatch_plan`, `collect_plan`, `verify_plan`, `reassign_task`, and `mark_integrated` functions; it never owns Worker execution, verification, integration evidence, or Task lifecycle. A typed external-text reference remains a Handoff Payload reference and is never fetched or promoted to Control.

**Tech Stack:** Python 3.10+, standard-library dataclasses/JSON/datetime/URL parsing, existing pytest, CommanderPlanStore, DevFarmOrchestrator, HandoffEnvelope.

**Spec:** `docs/superpowers/specs/2026-09-13-codex-supervised-dogfood-design.md`

## Global Constraints

- Keep `v2/bootstrap` as the implementation branch; do not alter frozen `main`.
- Reuse the existing Commander Plan, DevFarm manifest, attempt artifact, Host Verification, protected-path, integration, and bounded-attempt authorities.
- Do not create a production scheduler, Task state machine, Agent framework, detached Worker process manager, OpenAI/Claude API connection, Compression Service connection, MCP server, Discord bot, auto-merge, auto-push, or deployment path.
- Keep Host Verification `STATIC_ONLY` by default. External Worker code reaches `HOST_VERIFIED` / integration only through existing `TRUSTED_HOST_EXEC` attempt approval or `OS_SANDBOXED`; this plan must not weaken that boundary.
- A Scheduled heartbeat is an app-level integration. Repo code exposes a compact desired cadence and wake state but never calls an external automation API.
- Supported cadence values are exactly 1, 5, 10, and 15 minutes. One active heartbeat only; no concurrent timer set.
- Use TDD: every production behavior starts with a focused test that has been observed failing.
- Run focused tests after each task; run `python -m pytest tests/v2 -q`, `python scripts/check_architecture.py`, and `python -m compileall -q src scripts` before the final documentation/evidence commit.

---

## File and interface map

| File | Responsibility |
| --- | --- |
| `src/dev_agent/handoff/references.py` | Typed, metadata-only `ExternalTextReference` validation and serialization. No HTTP fetch/upload. |
| `src/dev_agent/handoff/protocol.py` | Recognize and preserve an external-text Payload reference while keeping legacy generic references compatible. |
| `src/dev_agent/handoff/presets.py` | Build a reference-first rework handoff from task/attempt/failure/review references. |
| `scripts/devfarm_supervisor_protocol.py` | Pure typed Supervisor metadata, cadence selection/escalation, compact wake-record validation. No Commander imports. |
| `scripts/devfarm_commander.py` | Preserve optional validated `supervisor` metadata in the canonical Commander Plan and its existing CAS writes. |
| `scripts/devfarm_supervisor.py` | Development-only facade that performs one bounded Commander composition pass and returns compact wake/status output. |
| `docs/CODEX_SUPERVISOR.md` | Durable operator/heartbeat instruction: quiet checks, cadence update, review boundary, pause conditions. |
| `docs/requirements/model-handoff/04-codex-supervised-dogfood.md` | Formal development-only Supervisor and external-reference contract. |
| `docs/requirements/model-handoff/00-index.md`, `docs/CURRENT_STATE.md`, `docs/V2_EXECUTION_PLAN.md`, `docs/SYSTEM_MAP.md`, `spec/v2/05_api_spec.md`, `spec/v2/06_implementation_spec.md`, `spec/v2/TRACEABILITY.md` | Source-of-truth pointers and evidence, updated only after verified implementation. |
| `tests/v2/test_handoff_references.py` | External reference contract and Payload/Control isolation tests. |
| `tests/v2/test_devfarm_supervisor.py` | Supervisor metadata, cadence, bounded composition, wake, review/rework, integration-delegation, and restart tests. |
| `scripts/test_scope.py` | Map Supervisor/Handoff source changes to the focused test cluster. |

### Task 1: Add typed external Payload references and rework handoff

**Files:**
- Create: `src/dev_agent/handoff/references.py`
- Modify: `src/dev_agent/handoff/protocol.py`
- Modify: `src/dev_agent/handoff/presets.py`
- Modify: `src/dev_agent/handoff/__init__.py`
- Test: `tests/v2/test_handoff_references.py`
- Test: `tests/v2/test_handoff.py`

**Interfaces:**
- Consumes: `HandoffEnvelope`, `PayloadMode.REFERENCE`, `HandoffDirective`, existing `HandoffKind` and `HandoffRole`.
- Produces: `ExternalTextReference`, `external_text_reference(...)`, and `rework_request(...)` without HTTP I/O or authority changes.

- [ ] **Step 1: Write failing contract tests for an external text reference**

```python
def test_external_text_reference_round_trips_as_reference_payload():
    reference = ExternalTextReference(
        location="https://example.invalid/payload/abc",
        sha256="a" * 64,
        size=128,
        created_at="2026-09-13T00:00:00+00:00",
        expires_at="2026-09-14T00:00:00+00:00",
        source_label="temporary-text",
    )
    envelope = HandoffEnvelope(
        kind="implementation_instruction",
        subject="narrow repair",
        instruction="apply the referenced repair",
        source_role="planner",
        target_role="executor",
        payload_mode="reference",
        payload_reference=reference.to_dict(),
        directive=HandoffDirective(exclusions=("Compression APIへ接続しない",)),
    )

    restored = HandoffEnvelope.from_dict(envelope.to_dict())

    assert restored.payload_reference == reference.to_dict()
    assert restored.directive == envelope.directive
```

Add parameterized rejection cases for `http`, URL userinfo, query/fragment,
missing/invalid SHA-256, zero/oversized size, non-ISO timestamps, and
`expires_at <= created_at`. Add a test that `payload_for_compression()` receives
only `envelope.payload`, never `payload_reference` or `directive`.

- [ ] **Step 2: Run the new reference tests and verify RED**

Run: `python -m pytest tests/v2/test_handoff_references.py -q`

Expected: FAIL because `ExternalTextReference` and the rework preset do not yet
exist, rather than due to test fixture or import syntax errors.

- [ ] **Step 3: Implement the metadata-only reference type**

Create the frozen dataclass and exact conversion methods:

```python
@dataclass(frozen=True)
class ExternalTextReference:
    location: str
    sha256: str
    size: int
    created_at: str
    expires_at: str | None = None
    source_label: str | None = None

    def to_dict(self) -> dict[str, object]: ...

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ExternalTextReference": ...
```

Require absolute `https` locations with no credentials, query, or fragment;
lowercase 64-hex digest; positive size at most `10_000_000`; and optional
expiry strictly after creation. `to_dict()` must always emit
`{"type": "external_text", ...}`. Do not add a downloader, uploader, network
call, or secret exception.

In `HandoffEnvelope.__post_init__`, when the existing mapping reference has
`type == "external_text"`, parse and normalize it through
`ExternalTextReference.from_dict()`. Preserve all non-external mapping
references unchanged for backward compatibility.

Add `rework_request(...)` in presets. It must build a `repair_request` from
Planner/Reviewer to Executor with `payload_mode="reference"`; the mapping has
only `original_task`, `attempt_result`, `failure_evidence`, and
`review_findings` references. The directive carries required correction and
existing exclusion/caution Control rather than copying failure log text into
Control.

- [ ] **Step 4: Run the Handoff cluster and verify GREEN**

Run: `python -m pytest tests/v2/test_handoff.py tests/v2/test_handoff_directives.py tests/v2/test_handoff_references.py tests/v2/test_compression.py -q`

Expected: PASS, including legacy generic `payload_reference` round trips and
the existing compression isolation tests.

- [ ] **Step 5: Commit the independent Handoff reference slice**

```powershell
git add src/dev_agent/handoff/references.py src/dev_agent/handoff/protocol.py src/dev_agent/handoff/presets.py src/dev_agent/handoff/__init__.py tests/v2/test_handoff_references.py tests/v2/test_handoff.py
git commit -m "feat: add external handoff references"
```

### Task 2: Persist typed Supervisor metadata in Commander Plans

**Files:**
- Create: `scripts/devfarm_supervisor_protocol.py`
- Modify: `scripts/devfarm_commander.py`
- Test: `tests/v2/test_devfarm_supervisor.py`
- Test: `tests/v2/test_devfarm_commander.py`

**Interfaces:**
- Consumes: Commander `run_id`, Plan CAS through `CommanderPlanStore.save()`, JSON-safe roadmap reference, and an expected remaining Worker duration.
- Produces: `normalize_supervisor_metadata(value)`, `select_heartbeat_cadence(expected_remaining_seconds)`, `advance_heartbeat_cadence(metadata, unchanged)`, and Plan field `supervisor`.

- [ ] **Step 1: Write failing Supervisor metadata/cadence tests**

```python
def test_commander_plan_preserves_supervisor_metadata_with_cas(root, revision):
    plan = create_plan(root, {
        "run_id": "supervisor-plan",
        "objective": "bounded worker review",
        "base_revision": revision,
        "tasks": [{"task_id": "review", "owner": "codex", "ownership": ["docs/review.md"]}],
        "supervisor": {"status": "ACTIVE", "cadence_minutes": 1, "unchanged_check_limit": 2},
    })

    restored = CommanderPlanStore(root).load("supervisor-plan")

    assert restored["supervisor"]["cadence_minutes"] == 1
    assert restored["supervisor"]["unchanged_check_limit"] == 2
```

Add boundary tests for `select_heartbeat_cadence`: `300 -> 1`, `301 -> 5`,
`1_200 -> 5`, `1_201 -> 10`, `3_600 -> 10`, `3_601 -> 15`, and `None -> 15`.
Add a two-check default escalation test and a three-check configured escalation
test for `1 -> 5 -> 10 -> 15 -> 15`; assert state/artifact change resets the
unchanged count. Add invalid status, cadence, counter, wake record, roadmap
reference size, and `unchanged_check_limit` rejection cases.

- [ ] **Step 2: Run the targeted tests and verify RED**

Run: `python -m pytest tests/v2/test_devfarm_supervisor.py -q`

Expected: FAIL because the Supervisor metadata protocol and normalized Plan
field do not exist.

- [ ] **Step 3: Implement the pure Supervisor metadata protocol**

In `scripts/devfarm_supervisor_protocol.py`, keep imports to the Python
standard library. Define the exact allowed statuses and wake kinds from the
approved design. Normalize this JSON-safe shape:

```python
{
    "schema_version": 1,
    "status": "ACTIVE",
    "roadmap_reference": {},
    "roadmap_position": None,
    "cadence_minutes": 15,
    "unchanged_check_limit": 2,
    "unchanged_check_count": 0,
    "overall_deadline": None,
    "next_action": "advance",
    "wake_events": [],
    "metrics": {
        "codex_wake_count": 0, "codex_review_count": 0,
        "worker_dispatch_count": 0, "worker_success_count": 0,
        "worker_retry_count": 0, "payload_inline_bytes": 0,
        "payload_reference_bytes": 0, "artifact_fetch_count": 0,
    },
}
```

Limit wake records to 64 entries; deduplicate them by a stable event identity
made from `kind`, task ID, attempt ID, and patch digest. A wake record may
contain only the compact fields enumerated in the design; reject raw payload
or a Worker conversation field.

In `scripts/devfarm_commander.py`, import only
`normalize_supervisor_metadata`. If a `supervisor` object is supplied,
normalize it into the return value of `validate_plan()`. If omitted, omit it
from normalized legacy Plans. Since `refresh_plan()` validates and deep-copies
the Plan, the metadata then survives every existing Plan mutation and CAS save.

- [ ] **Step 4: Run Commander and Supervisor metadata tests and verify GREEN**

Run: `python -m pytest tests/v2/test_devfarm_supervisor.py tests/v2/test_devfarm_commander.py -q`

Expected: PASS. Existing plans without `supervisor` remain valid and Plan
revision conflicts still reject lost updates.

- [ ] **Step 5: Commit the Plan metadata slice**

```powershell
git add scripts/devfarm_supervisor_protocol.py scripts/devfarm_commander.py tests/v2/test_devfarm_supervisor.py tests/v2/test_devfarm_commander.py
git commit -m "feat: persist commander supervisor metadata"
```

### Task 3: Compose one bounded Supervisor pass and explicit review operations

**Files:**
- Create: `scripts/devfarm_supervisor.py`
- Modify: `src/dev_agent/handoff/presets.py`
- Test: `tests/v2/test_devfarm_supervisor.py`

**Interfaces:**
- Consumes: `CommanderPlanStore`, `dispatch_plan`, `collect_plan`, `verify_plan`, `reassign_task`, `mark_integrated`, `DevFarmOrchestrator`, Provider map, and `SupervisorMetadata` helpers.
- Produces: `CodexSupervisedCommanderRun.create()`, `.advance()`, `.status()`, `.rework_handoff()`, `.reassign()`, `.approve_integration()`, and a compact JSON-serializable `SupervisorStep`.

- [ ] **Step 1: Write failing bounded-composition tests**

```python
def test_advance_dispatches_verifies_and_stops_for_codex_review(root, revision, provider, trusted_orchestrator):
    create_supervised_worker_plan(root, revision)
    supervisor = CodexSupervisedCommanderRun(root, "supervisor-run")

    step = supervisor.advance(
        providers={"worker-a": provider},
        orchestrator=trusted_orchestrator,
        expected_remaining_seconds=120,
    )

    assert step.status == "REVIEW_REQUIRED"
    assert [event["kind"] for event in step.wake_events] == ["HOST_VERIFIED_RESULT_READY"]
    assert step.plan["tasks"][0]["status"] == "HOST_VERIFIED"
    assert step.cadence_minutes == 1
```

Add tests that: repeated unchanged `DISPATCHED` Plan reads create no new wake;
the second unchanged default check promotes cadence 1 to 5; the next state or
artifact reference resets the counter; attempt-limit rejection yields only
`WORKER_REJECTED_AFTER_RETRY`; a missing Provider yields
`NO_ELIGIBLE_WORKER`; a Host-verified task never calls `mark_integrated` from
`advance`; a rework Handoff contains references, not full attempt content; and
`approve_integration()` delegates the exact arguments to existing
`mark_integrated()` after an independently created Git integration revision.

- [ ] **Step 2: Run the Supervisor tests and verify RED**

Run: `python -m pytest tests/v2/test_devfarm_supervisor.py -q`

Expected: FAIL because `CodexSupervisedCommanderRun` and `SupervisorStep` do
not exist.

- [ ] **Step 3: Implement the thin facade without a loop or detached Worker**

Implement these minimum public methods:

```python
class CodexSupervisedCommanderRun:
    def __init__(self, root: str | Path, run_id: str) -> None: ...
    def create(self, *, roadmap_reference: Mapping[str, object], expected_remaining_seconds: int | None = None, unchanged_check_limit: int = 2) -> dict[str, object]: ...
    def status(self) -> SupervisorStep: ...
    def advance(self, *, providers: Mapping[str, ModelProvider], orchestrator: DevFarmOrchestrator | None = None, expected_remaining_seconds: int | None = None) -> SupervisorStep: ...
    def rework_handoff(self, task_id: str, *, review_findings_reference: Mapping[str, object], required_correction: str) -> HandoffEnvelope: ...
    def reassign(self, task_id: str, *, provider_id: str, model_id: str, provider_binding_id: str | None = None) -> SupervisorStep: ...
    def approve_integration(self, task_id: str, *, note: str, target_ref: str, integration_revision: str, source_attempt_id: str, verified_patch_digest: str) -> SupervisorStep: ...
```

`advance()` performs one pass only: refresh/load; dispatch existing `READY`
worker tasks; collect artifacts; verify existing `PROPOSED` work; reload; emit
deduplicated compact wakes; update cadence/metrics through Plan CAS; return.
It never calls `mark_integrated`, does not iterate until idle, and does not
start a background process. A synchronous `dispatch_plan()` remains a bounded
process wait rather than a source of repeated Codex reasoning.

Map already-persisted Plan facts to the approved Supervisor projection. Count
only actual dispatch calls, terminal accepted Worker results, explicit
reassignments, review wake creation, inline payload byte length, declared
external-reference size, and actual explicit artifact retrieval. Do not infer
model token usage.

- [ ] **Step 4: Run focused Supervisor/Commander/Handoff tests and verify GREEN**

Run: `python -m pytest tests/v2/test_devfarm_supervisor.py tests/v2/test_devfarm_commander.py tests/v2/test_handoff_references.py tests/v2/test_handoff_cycle.py -q`

Expected: PASS. A restart reconstructs the same run from the Plan's
`supervisor` metadata and emits no duplicate wake for the same verified
attempt.

- [ ] **Step 5: Commit the Supervisor composition slice**

```powershell
git add scripts/devfarm_supervisor.py src/dev_agent/handoff/presets.py tests/v2/test_devfarm_supervisor.py
git commit -m "feat: add codex supervised commander run"
```

### Task 4: Add compact CLI status and heartbeat operating contract

**Files:**
- Modify: `scripts/devfarm_supervisor.py`
- Create: `docs/CODEX_SUPERVISOR.md`
- Modify: `scripts/test_scope.py`
- Test: `tests/v2/test_devfarm_supervisor.py`
- Test: `tests/v2/test_test_scope.py`

**Interfaces:**
- Consumes: `CodexSupervisedCommanderRun.status()` / `.advance()` and the app-level Scheduled task boundary.
- Produces: `python scripts/devfarm_supervisor.py status|resume ...` compact JSON and a durable instruction for a single current-thread heartbeat.

- [ ] **Step 1: Write failing CLI and cadence-operation tests**

```python
def test_supervisor_status_cli_returns_compact_wake_and_next_schedule(root, capsys):
    create_supervised_worker_plan(root, revision_for(root))
    CodexSupervisedCommanderRun(root, "supervisor-run").create(
        roadmap_reference={"path": "docs/V2_EXECUTION_PLAN.md"},
        expected_remaining_seconds=None,
    )

    assert supervisor_main(["status", "supervisor-run", "--root", str(root)]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["cadence_minutes"] == 15
    assert output["wake_events"] == []
    assert "tasks" not in output["compact_context"]
```

Add a `resume` CLI test using a FakeProvider only. Assert it returns the
compact status/next cadence and does not print raw patch, test output, model
text, environment data, or a Worker result object. Add a focused test-scope
map assertion for `scripts/devfarm_supervisor*.py` and
`src/dev_agent/handoff/references.py`.

- [ ] **Step 2: Run the CLI/test-scope tests and verify RED**

Run: `python -m pytest tests/v2/test_devfarm_supervisor.py tests/v2/test_test_scope.py -q`

Expected: FAIL because no Supervisor CLI or test-scope mapping exists.

- [ ] **Step 3: Implement compact CLI and write the operating contract**

Add `status` and `resume` argparse subcommands in
`scripts/devfarm_supervisor.py`. `status` only reads the Plan and returns
`run_id`, Supervisor status, cadence, next action, wake count, compact wake
records, metrics, and reference paths. `resume` creates the existing qualified
Providers through `dispatch_cli`-equivalent existing DevFarm activation logic,
then calls `.advance()`; it must retain the existing verification trust default
and require an explicit `--operator-approved` only when an operator selects
`TRUSTED_HOST_EXEC`.

Write `docs/CODEX_SUPERVISOR.md` with the heartbeat instruction:

```text
Read only the compact Supervisor status. If no meaningful wake exists, do not
read artifacts, do not draft an answer, and schedule exactly one next heartbeat
at cadence_minutes. After unchanged_check_limit unchanged results, use the next
longer cadence. Pause the heartbeat on COMPLETED, BLOCKED, or
HUMAN_DECISION_REQUIRED. Review only HOST_VERIFIED evidence; never auto-merge.
```

Document that the desktop Scheduled task is external to the repo, uses one
current-thread heartbeat, and is updated to the emitted cadence. It must not
claim automatic direct Worker-completion wakeup or a zero-cost/unlimited Codex
execution channel.

- [ ] **Step 4: Run the compact CLI and affected-test checks and verify GREEN**

Run: `python -m pytest tests/v2/test_devfarm_supervisor.py tests/v2/test_test_scope.py -q`

Expected: PASS with no raw artifact in CLI output. Run:
`python scripts/test_scope.py scripts/devfarm_supervisor.py` and verify the
output includes the Supervisor, Commander, DevFarm, and Handoff focused tests.

- [ ] **Step 5: Commit the CLI/operating-contract slice**

```powershell
git add scripts/devfarm_supervisor.py scripts/test_scope.py docs/CODEX_SUPERVISOR.md tests/v2/test_devfarm_supervisor.py tests/v2/test_test_scope.py
git commit -m "feat: add supervisor heartbeat interface"
```

### Task 5: Synchronize formal docs and prepare bounded Dogfood evidence

**Files:**
- Create: `docs/requirements/model-handoff/04-codex-supervised-dogfood.md`
- Modify: `docs/requirements/model-handoff/00-index.md`
- Modify: `docs/CODEX_COMMANDER.md`
- Modify: `docs/DEVFARM.md`
- Modify: `docs/SYSTEM_MAP.md`
- Modify: `docs/V2_EXECUTION_PLAN.md`
- Modify: `spec/v2/05_api_spec.md`
- Modify: `spec/v2/06_implementation_spec.md`
- Modify: `spec/v2/TRACEABILITY.md`
- Modify: `docs/CURRENT_STATE.md`
- Test: `tests/v2/test_architecture_script.py`

**Interfaces:**
- Consumes: verified Supervisor/Handoff tests, current plan state, Host Verification trust level, and GitHub exact-head results.
- Produces: formal REQ traceability and an honest operational procedure for a real Free Worker Dogfood run.

- [ ] **Step 1: Write a failing traceability/architecture assertion if a new protected development boundary needs coverage**

```python
def test_architecture_allows_supervisor_to_depend_on_commander_but_not_runtime_scheduler():
    result = run_architecture_check_for("scripts/devfarm_supervisor.py")

    assert result.returncode == 0
    assert "ARCHITECTURE_PASS" in result.stdout
```

If the existing architecture test already covers the dependency direction,
add no duplicate production test; record the exact existing assertion and run
it as part of the final Gate instead.

- [ ] **Step 2: Run the architecture test and establish the expected outcome**

Run: `python -m pytest tests/v2/test_architecture_script.py -q`

Expected: PASS if the new script follows existing development-only import
boundaries; otherwise FAIL only for the newly introduced forbidden import.

- [ ] **Step 3: Document the implemented boundary without overclaiming Dogfood**

Add the new requirement chapter and index link. It must state:

- Codex is a Supervisor and Free Workers are preferred for narrow work;
- `supervisor` metadata is Commander Plan state, not a production state
  machine;
- only meaningful compact events wake Codex;
- one heartbeat selects 1/5/10/15 minutes and promotes after 2 or 3 unchanged
  checks;
- a direct custom completion event is not currently available;
- ExternalTextReference is metadata only, untrusted Payload, and never fetched
  by this slice;
- `STATIC_ONLY` external Worker results cannot be accepted/integrated;
- Scheduled-task creation/update/pause is an app-level, post-implementation
  operation.

Add a new `REQ-069` traceability record pointing to the Supervisor protocol,
facade, Handoff reference type, and focused tests. Update Current State only
with actual local test output, exact implementation commit, and exact-head CI
after it is observed. Do not mark external Worker integration Dogfood complete
until an attempt has the existing explicit trust evidence.

- [ ] **Step 4: Run all required final Gates**

Run:

```powershell
python -m pytest tests/v2 -q
python scripts/check_architecture.py
python -m compileall -q src scripts
```

Expected: the complete v2 suite passes, the architecture output is
`ARCHITECTURE_PASS`, and compileall has no output. Commit the evidence docs
only after recording the actual results.

- [ ] **Step 5: Create the first bounded external-Worker Dogfood Plan only after explicit attempt approval**

Choose one non-protected, narrow task from the current roadmap. Record a
Commander Plan where the Worker owns a non-overlapping file scope and Codex
owns review/integration. Dispatch only a currently qualified and billing
admitted Free Worker. Under default `STATIC_ONLY`, record its proposal and
compact Supervisor wake but do not treat it as accepted. Before an executable
Host Verification of external generated code, obtain the existing
attempt-scoped `TRUSTED_HOST_EXEC` operator approval or use an available
`OS_SANDBOXED` runner. Then use `approve_integration()` only after a Codex
review and an independently created Git revision satisfies
`mark_integrated()`.

- [ ] **Step 6: Commit docs/evidence and push; verify exact-head CI**

```powershell
git add docs/requirements/model-handoff/04-codex-supervised-dogfood.md docs/requirements/model-handoff/00-index.md docs/CODEX_COMMANDER.md docs/DEVFARM.md docs/SYSTEM_MAP.md docs/V2_EXECUTION_PLAN.md spec/v2/05_api_spec.md spec/v2/06_implementation_spec.md spec/v2/TRACEABILITY.md docs/CURRENT_STATE.md
git commit -m "docs: record supervisor dogfood boundary"
git push origin v2/bootstrap
gh run list --branch v2/bootstrap --limit 10
```

Expected: the pushed exact commit receives successful `v2-core` and
`v2-provider-smoke` runs. If GitHub branch protection cannot be configured,
record it as an external deployment condition rather than claiming it in code.

## Plan self-review

- Spec coverage: Tasks 1–4 cover external references, durable Plan metadata,
  wake/cadence, bounded composition, review/rework/integration delegation,
  compact CLI, and heartbeat operation. Task 5 covers formal requirements,
  evidence, and the separately gated real Worker Dogfood path.
- Scope: no task creates a scheduler, automatic model launcher, direct event
  subscription, compression service, provider API, or automatic integration.
- Type consistency: `ExternalTextReference`, `SupervisorMetadata` normalization,
  `CodexSupervisedCommanderRun`, `SupervisorStep`, `rework_request`, and the
  Plan `supervisor` field are introduced before later tasks consume them.
- Placeholder scan: no incomplete implementation instruction is left in this
  plan; real external code execution is intentionally represented as an
  existing approval gate rather than an omitted behavior.
