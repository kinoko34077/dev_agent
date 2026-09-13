# Documentation inventory

この一覧は、2026-09-13に`rg --files docs spec/v2`で生成した現行文書の分類である。これは文書の役割を示すInventoryであり、要求やGateの代替ではない。

## Canonical current

| Path | Role |
| --- | --- |
| `docs/CURRENT_STATE.md` | 現在のHEAD、検証結果、成立/未成立、blocker、直近Target |
| `docs/V2_EXECUTION_PLAN.md` | Phase、現在の大きなFrontier、Operational Acceptance、次Gate |
| `docs/V2_DETAILED_ROADMAP.md` | D0–D9とPhase 8/9の詳細順序・Gate |
| `docs/SYSTEM_MAP.md` | 所有責務と依存境界 |
| `docs/CODEX_COMMANDER.md` | Commanderの恒久運用契約 |
| `docs/CODEX_SUPERVISOR.md` | Supervisorの恒久運用契約 |
| `docs/CODEX_DAILY_DOGFOOD.md` | 日常操作手順 |
| `docs/DEVFARM.md` | Worker/Host Verification境界 |

## Stable requirements

`docs/requirements/**` は要求、不変条件、acceptance、authority、behaviorのみを保持する。Current State、HEAD、現在のNext Taskはここへ追加しない。

## ADR / evidence / traceability

| Path | Role |
| --- | --- |
| `spec/v2/adr/**` | 設計判断とdecision rationale |
| `spec/v2/evidence/**` | 観測済みEvidence JSON |
| `spec/v2/TRACEABILITY.md` | requirement → implementation → test → evidence |
| `spec/v2/GATE_STATUS.json` | Gate schema/statusの正本。証拠状態を自己昇格させない |
| `spec/v2/G6O1_DEFERRED.md` | G6O1の未検証・凍結・再開手順 |

## Active working plan

| Path | Role |
| --- | --- |
| `docs/superpowers/plans/2026-09-13-planner-to-worker-e2e.md` | D1/D2の現在の狭い実行Plan。詳細順序は`V2_DETAILED_ROADMAP.md`を参照 |

## Historical archive

`docs/archive/current-state/**`、`docs/archive/roadmaps/**`、`docs/archive/plans/**`、`docs/archive/specs/**` は過去の作業Plan/状態/設計の参照用であり、現在の正本ではない。archive内の記録を現在のGateやNext Taskの根拠へ再利用する場合は、現行Evidenceと照合する。

2026-09-13時点でarchiveへ移した完了/旧Plan:

- `docs/archive/plans/2026-09/2026-09-08-*` — Phase 3.5 / Phase 6 foundation
- `docs/archive/plans/2026-09/2026-09-09-*` — provider/Phase 6 transition
- `docs/archive/plans/2026-09/2026-09-10-*` — AgentBackend dispatch
- `docs/archive/plans/2026-09/2026-09-11-*` — hardening/refactor/provider binding
- `docs/archive/plans/2026-09/2026-09-12-*` — Codex dogfood/model handoff
- `docs/archive/plans/2026-09/2026-09-13-codex-supervised-dogfood.md`
- `docs/archive/plans/2026-09/2026-09-13-daily-supervisor-operation.md`
- `docs/archive/plans/2026-09/2026-09-13-supervisor-operation-and-worker-probes.md`
- `docs/archive/plans/2026-09/2026-09-13-supervisor-runtime-hardening.md`

2026-09-13時点の`docs/archive/specs/2026-09/2026-09-13-*.md`は、既存のSupervisor契約のdecision rationaleを保持するため archiveへ移した。恒久契約は`CODEX_COMMANDER.md`、`CODEX_SUPERVISOR.md`、requirements、ADRを正本とする。

## Mechanical inventory command

```powershell
rg --files docs spec/v2 | Sort-Object
```

このInventoryの分類を変更する場合は、先にcanonical文書のreferenceを更新し、stale linkを確認する。
