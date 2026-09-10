# v2 System Map

この表はCodex Commanderが最初に読む軽量な責務・所有権マップです。詳細な
受入条件は `spec/v2/`、現在の実装状態は `CURRENT_STATE.md` を参照します。

| 領域 | 主な場所 | 責務 / 公開入口 | 依存方向 | 保護責務 | 主なspec / test |
| --- | --- | --- | --- | --- | --- |
| Domain | `src/dev_agent/domain/` | Task、Model、Tool、Event protocol | 下層なし | protocol意味 | `spec/v2/PROTOCOL.md`, `tests/v2/test_protocol.py` |
| Runtime | `src/dev_agent/runtime/` | Controller、checkpoint、resume、Model turn | domain / state / providers / resources | lifecycle、transition | `tests/v2/test_runtime_*.py` |
| Scheduler | `src/dev_agent/scheduler/` | Durable Queue、Worker、lease、wake | state / runtime | ownership、fencing | `tests/v2/test_phase6_scheduler.py` |
| State | `src/dev_agent/state/` | SQLite/JSON durable state、transaction | domain | `commit_transition()` atomicity | `tests/v2/test_recovery_sqlite.py` |
| Providers | `src/dev_agent/providers/` | Adapter、Factory、Registry、Dispatcher、journal | domain / resources | Provider intent、audit、reconciliation | `tests/v2/test_phase6_provider_*.py` |
| Resources | `src/dev_agent/resources/` | Resource、quota、health、router、budget | domain / state | Hard Budget、quota、privacy、survival | `tests/v2/test_resource_*.py`, `test_quota_*.py` |
| Tools | `src/dev_agent/tools/` | policy、executor、subprocess、effect guard | domain / state | approval、scope、process containment | `tests/v2/test_tool_*.py` |
| Intelligence | `src/dev_agent/intelligence/` | tier policy、Evaluator、escalation、lifecycle | domain / providers / resources / state | finite execution、explicit review | `tests/v2/test_intelligence_*.py` |
| AgentBackend | `src/dev_agent/backends/` | thin typed contract、effect intent接続dispatcher | domain / state | dispatch authorityは注入Control Plane、contractは所有しない | `tests/v2/test_agent_backend_protocol.py`, `test_agent_backend_dispatcher.py`, requirements §20-22 |
| Recovery | `recovery/` | runtime-independent diagnose、backup、restore、rollback | durable artifacts / Git | external recovery authority | `tests/v2/test_recovery*.py` |
| Operation | `src/dev_agent/operation.py`, `src/dev_agent/__main__.py` | `start`、`submit`、`status`、`stop` | existing runtime stack | no CLI-owned state | `tests/v2/test_operation.py` |
| DevFarm | `scripts/devfarm*.py` | manifest、proposal、Host Verification、metrics | ProviderFactory + Git artifacts | outbound scope、worktree、patch | `tests/v2/test_devfarm_*.py` |
| Commander | `scripts/devfarm_commander.py` | development-only parent Plan、DAG、dispatch、collect、verify | existing DevFarm only | ownership、bounded reassign | `tests/v2/test_devfarm_commander.py` |
| DevFarm data | `.devfarm/` | plans、tasks、results、worktrees、metrics | ignored local artifacts | never source/Gate authority | `docs/DEVFARM.md` |
| Formal spec | `spec/v2/` | requirements、ADR、Gate、traceability、schemas | documentation | Gate promotion evidence | `spec/v2/GATE_STATUS.json` |
| Current state | `docs/CURRENT_STATE.md` | implementation baseline、tests、live state | evidence references | no duplicated authority | this document / changelog |

## Dependency constraints

`domain` is foundational. `runtime` composes state, provider, resource, and
tool boundaries. `scheduler` owns queue/lease mechanics; `recovery` remains
independent of Controller and Scheduler. `DevFarm` and `Commander` are
development tooling and do not become Production Runtime components.

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
authority. A new external AgentBackend, MCP, A2A, or UI integration must be an
adapter around these boundaries, not a replacement for them.
