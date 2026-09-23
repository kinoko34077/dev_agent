# v2 System Map

この表はCodex Commanderが最初に読む軽量な責務・所有権マップです。詳細な
受入条件は `spec/v2/`、現在の実装状態は `CURRENT_STATE.md` を参照します。

| 領域 | 主な場所 | 責務 / 公開入口 | 依存方向 | 保護責務 | 主なspec / test |
| --- | --- | --- | --- | --- | --- |
| Domain | `src/dev_agent/domain/` | Task、Model、Tool、Event protocol | 下層なし | protocol意味 | `spec/v2/PROTOCOL.md`, `tests/v2/test_protocol.py` |
| Runtime | `src/dev_agent/runtime/` | Controller、checkpoint、resume、Model turn | domain / state / providers / resources | lifecycle、transition | `tests/v2/test_runtime_*.py` |
| Scheduler | `src/dev_agent/scheduler/` | Durable Queue、Worker、lease、wake | state / runtime | ownership、fencing | `tests/v2/test_phase6_scheduler.py` |
| Persistence primitive | `src/dev_agent/persistence/lease.py` | State／Scheduler shared lease proof and stale-lease assertion | domain / SQLite primitive | atomic lease fencing | `tests/v2/test_lease_fencing_architecture.py` |
| State | `src/dev_agent/state/` | SQLite/JSON durable state、transaction、narrow state views、control repository | domain / persistence primitive | `commit_transition()` atomicity | `tests/v2/test_recovery_sqlite.py`, `test_lease_fencing_architecture.py` |
| Providers | `src/dev_agent/providers/` | Adapter、Factory、Registry、Dispatcher、journal。Gemini key-slot bindings、Ollama Cloud (`ollama_cloud`)、Vercel AI Gateway (`vercel`) は既存のOpenAI互換境界を利用し、local Ollama (`ollama`) と分離 | domain / resources | Provider intent、audit、reconciliation、credential env identity | `tests/v2/test_phase6_provider_*.py`, `test_provider_factory.py`, `test_openai_compatible_http.py` |
| Resources | `src/dev_agent/resources/` | ResourceLedger facade、catalog／observation／quota／health／budget、qualification projection、explicit legacy repair、router、schema/migrations | domain / state | Hard Budget、quota、privacy、qualification、survival | `tests/v2/test_resource_*.py`, `test_quota_*.py`, `test_resource_repair.py` |
| Tools | `src/dev_agent/tools/` | policy、executor、subprocess、effect guard | domain / state | approval、scope、process containment | `tests/v2/test_tool_*.py` |
| Security | `src/dev_agent/security/protected_paths.py`, `src/dev_agent/security/` | protected responsibility path、PathPolicy、audit sanitizer、Host egress manifest | domain / policy | protected authority、secret boundary、outbound content gate | `tests/v2/test_security_boundaries.py`, `tests/v2/test_egress_policy.py` |
| Intelligence | `src/dev_agent/intelligence/` | tier policy、Evaluator、escalation、lifecycle | domain / providers / resources / state | finite execution、explicit review | `tests/v2/test_intelligence_*.py` |
| AgentBackend | `src/dev_agent/backends/` | thin typed contract、effect intent接続dispatcher | domain / state | dispatch authorityは注入Control Plane、contractは所有しない | `tests/v2/test_agent_backend_protocol.py`, `test_agent_backend_dispatcher.py`, requirements §20-22 |
| Handoff | `src/dev_agent/handoff/`, `scripts/handoff_cycle.py` | model-neutral Control/Payload、typed Directive、renderer、role handoff | domain / policy | Human authorityとControl/Payload境界 | `tests/v2/test_handoff.py`, `test_handoff_directives.py` |
| Human interaction | `src/dev_agent/human/` | `HumanInteractionPort`、durable `HumanRequest`/`HumanResponse`、`WAITING_HUMAN` park/resume | Operation / StateStore | exact request correlation、consume-once、no timeout auto-decision | `tests/v2/test_human_interaction.py`, `spec/v2/evidence/operational-human-assist-self-update-local-20260923.json` |
| Discord Human UI | `src/dev_agent/discord/` | optional discord.py Gateway ingress、active-run context routing、bounded renderer、Discord identity/scope、durable channel/thread pointer・scope・delivery metadata、`DiscordRuntimeComposition`による既存Operation／Process Coordination／Human／Approval境界へのrunner注入、read-only outbound progress/HumanRequest projection | Human / StateStore / Operation / Process Coordination callbacks | no Discord-owned Task/Scheduler/approval authority、unauthorized ingress silent、token redaction、exact response correlation、delivery-key idempotency、task-only cancel、structured scope handoff | `tests/v2/test_discord_adapter.py`, `test_discord_bot.py`, `test_discord_composition.py`, `test_discord_core_adapter.py`, `test_discord_durable_flow.py`, `test_discord_outbound.py`, `spec/v2/evidence/discord-gateway-login-20260923.json`, `spec/v2/evidence/discord-core-runner-composition-20260923.json`, `spec/v2/evidence/discord-outbound-projection-20260923.json`, `spec/v2/evidence/discord-operational-routing-20260923.json` |
| Compression | `src/dev_agent/compression/` | 固定`semantic-dense-v1`のpayload-only HTTP client、digest/provenance、retention warning | Handoff / external service boundary | Control非圧縮、固定endpoint/token、G6O1非接続 | `tests/v2/test_compression.py`, `spec/v2/evidence/compression-service-connection-20260914.json` |
| MCP adapter | `src/dev_agent/mcp/`, `scripts/devfarm_mcp.py` | bounded transport-neutral Supervisor adapter、Human Proxy、Expert Assist JSON-lines adapter | Commander / Supervisor public API / Human port | Human Proxy=`HUMAN_REQUIRED`、Expert=`PROPOSAL_ONLY`、MCP側にscheduler/budget/approval/integration authorityなし | `tests/v2/test_mcp_runtime.py`, `tests/v2/test_codex_human_mcp.py`, `spec/v2/evidence/operational-human-assist-self-update-local-20260923.json` |
| Recovery | `recovery/` | runtime-independent diagnose、backup、restore、rollback | durable artifacts / Git | external recovery authority | `tests/v2/test_recovery*.py` |
| Operation | `src/dev_agent/operation.py`, `operation_bootstrap.py`, `operation_planning.py`, `operation_runtime.py`, `cli.py`, `src/dev_agent/__main__.py`, `scripts/devfarm_runtime_coordinator.py` | `start`、`submit`、`status`、`stop`と既存componentのcomposition。薄いlocal runtime boundaryはpeer heartbeatと既存Operation 1周期を呼ぶ | existing runtime stack / state control / Process Coordination presence | no CLI-owned state、no second scheduler | `tests/v2/test_operation.py`, `test_operation_boundaries.py`, `test_operation_runtime.py` |
| DevFarm | `scripts/devfarm*.py`（`devfarm_codex.py`を含む） | manifest、proposal、Codex AgentBackend attempt、Host Verification、metrics | ProviderFactory + Git artifacts | outbound scope、worktree、patch、no auto-integration | `tests/v2/test_devfarm_*.py` |
| Commander | `scripts/devfarm_commander.py` | development-only parent Plan、DAG、dispatch、collect、verify | existing DevFarm only | ownership、bounded reassign | `tests/v2/test_devfarm_commander.py` |
| Supervisor | `scripts/devfarm_supervisor.py`, `scripts/devfarm_supervisor_protocol.py` | bounded `advance` snapshot、blocking `run_until_intervention`、cadence、compact wake/review evidence、rework handoff | existing Commander + Handoff | no auto-integration、no second scheduler | `tests/v2/test_devfarm_supervisor.py`, `docs/CODEX_SUPERVISOR.md` |
| Process Coordination | `src/dev_agent/coordination/` | peer identity/generation、presence lease、durable mailbox、immutable handoff artifacts、Work Address/Resume projection、generation-fenced ControlRequest、bounded Guardian action journal/evaluation、静的Guardian process execution、drain、pinned release、rolling/rollback/self-update composition | SQLite primitive / security audit / Host runtime | no Task/Scheduler/LLM process authority; Guardian accepts only Host-bound profiles and closes uncertain effects to UNKNOWN/reconciliation; failed candidate revisions are durably suppressed | `tests/v2/test_process_coordination_*.py`, `tests/v2/test_guardian_rolling.py`, `tests/v2/test_runtime_rollback.py`, `tests/v2/test_self_update.py`, `docs/requirements/process-coordination/`, `docs/CODEX_WORK_COORDINATION.md` |
| DevFarm data | `.devfarm/` | plans、tasks、results、worktrees、metrics | ignored local artifacts | never source/Gate authority | `docs/DEVFARM.md` |
| Formal spec | `spec/v2/` | requirements、ADR、Gate、traceability、schemas | documentation | Gate promotion evidence | `spec/v2/GATE_STATUS.json` |
| Current state | `docs/CURRENT_STATE.md` | implementation baseline、tests、live state | evidence references | no duplicated authority | this document / changelog |
| Refactor tooling | `scripts/check_architecture.py`, `scripts/test_scope.py` | dependency preflight、affected-test selection | repository read-only checks | no runtime authority | `tests/v2/test_architecture_script.py`, `test_test_scope.py` |

## Dependency constraints

`domain` is foundational. `persistence` contains only small SQLite safety
primitives shared by State and Scheduler. `runtime` composes state, provider, resource, and
tool boundaries. `scheduler` owns queue/lease mechanics; `recovery` remains
independent of Controller and Scheduler. `DevFarm` and `Commander` are
development tooling and do not become Production Runtime components.

Process Coordination is orthogonal to the Task Plane. Its store/protocol layer
holds peer/presence, mailbox delivery, immutable handoff references, and bounded
work-position projections; its separate Guardian execution adapter may invoke
only static Host-bound process profiles. The plane must not become a parallel
Scheduler, Task state machine, Provider router, Budget authority, arbitrary
command runner, or LLM process authority. Commander owns task-file ownership
and rejects overlap across unfinished plans before a new plan is persisted.

The local Operation runtime coordinator is only a process boundary around the
existing Operation loop. It does not own Task scheduling, wake policy, retry,
Provider calls, or approval; OS startup and deployed liveness remain a separate
Production Deployment track.

Host egress policy/manifest is a separate check at the existing DevFarm outbound
boundary. It may inspect and hash explicitly scoped files, but it does not grant
write, approval, Provider, or process authority. See
`docs/CODEX_WORK_COORDINATION.md` for the numbered operation contract.

The canonical provider path is:

```text
Controller -> ProviderDispatcher -> ProviderRegistry -> concrete Provider
```

The canonical development path is:

```text
Commander Plan -> manifest -> Remote proposal -> deterministic validation
  -> Host Verification worktree -> Codex review -> explicit integration
```

No layer may reach through a facade into another layer's private repository or
authority. Internal modules use leaf imports; package barrel exports exist only
for public compatibility. A new external AgentBackend, MCP, A2A, or UI integration
must be an adapter around these boundaries, not a replacement for them.

Production qualification admission requires an exact current identity and high
confidence. Low/medium confidence remains inspectable evidence but is not a
routing grant. Legacy Resource catalog changes use the explicit operator CLI
repair path and schema-v10 audit; normal Operation startup remains read-only.
