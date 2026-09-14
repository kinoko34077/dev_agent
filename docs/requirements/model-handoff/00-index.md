# Model Handoff / Compression Requirements

> Documentation role: stable Handoff/authority requirements only. Current
> implementation status and live evidence belong to `docs/CURRENT_STATE.md`
> and `spec/v2/evidence/**`; this index is not a progress log.

この章は、Human Specification Authority から Planner、Executor、Reviewer
へ渡す development/analysis handoff の共通形式を定義する。Production の
Task state、Scheduler、Budget、Provider routing、AgentBackend authorityを
置き換えない。

## 読み方

| 必要な確認 | 参照 |
| --- | --- |
| 現在の手動ループとauthority | [01-manual-loop-and-authority.md](01-manual-loop-and-authority.md) |
| Handoffのtyped契約とrenderer | [02-handoff-protocol.md](02-handoff-protocol.md) |
| Compression境界とG6O1-SIM/LIVE | [03-compression-and-g6o1.md](03-compression-and-g6o1.md) |

実装証跡は `spec/v2/TRACEABILITY.md`、現在状態は
`docs/CURRENT_STATE.md`、Gate判定は `spec/v2/GATE_STATUS.json` を正本とする。

## 追加仕様

| 内容 | 参照 |
| --- | --- |
| Codex Supervisor、bounded wait、reference-first dogfood | [04-codex-supervised-dogfood.md](04-codex-supervised-dogfood.md) |

## 実装段階

- 初期slice: `src/dev_agent/handoff/` のHandoffEnvelope、validation、
  `kinotch-ja-v1` renderer。
- 実装済みslice: development-onlyの`OneCycleDevelopmentLoop`が
  Human → Planner → 既存DevFarm/Codex attempt → Reviewer → Human境界で停止する。
- 実装済みslice: optionalな`HandoffDirective`が、exclusion、focus、payload
  semantics、比較、source/authority、continuation、output contractをControlとして
  表現する。最新の明示訂正は旧解釈を無効化し、renderer/preset/one-cycle requestへ
  伝わる。
- 実装済みslice: proposal-onlyな`ModelPlanningAdapter`が注入済みProviderの
  厳格JSONから`RootPlanningProposal`を復元し、明示されたexact intelligence tierを
  routing metadataへ渡せる。`DevelopmentPlanningBridge`は既存Host validatorを
  compositionしてCommander Plan候補を返すが、Task作成・`.devfarm`書込み・dispatchを
  モデルへ許可しない。
- 実装済みslice: Group Dの復旧境界は、Host/Controlが明示注入したtyped discovery
  authority receiptだけを`AgentBackendDispatcher`が受け付ける。identity／request
  fingerprint不一致やauthority未提供は`UNKNOWN`へ閉じ、Backendの`discover()`を暗黙に
  呼ばない。Proposal-only Free L2 shadowは実Free L2のstrict JSON proposalとHost
  validationまでboundedに実証済みであり、詳細は[`planner-l2-live-d1-20260914.json`](../../../spec/v2/evidence/planner-l2-live-d1-20260914.json)
  に記録する。Supervisor出力には既存Planから導出するdelegation summaryを追加した。
- 実装済みslice: `src/dev_agent/mcp/contracts.py`のboundedなtool／request／result契約に加え、
  `src/dev_agent/mcp/runtime.py`のtransport-neutral adapterと、既存Supervisorへ束ねる
  `scripts/devfarm_mcp.py`のdevelopment compositionを追加した。`status`、
  `artifact_summary`、`run`、`resume`、`review`、`rework`、`integrate`だけを既存authorityへ
  委譲し、wire transportとplanner proposal/apply authorityは未接続のままにする。
- 実装済みslice: `src/dev_agent/compression/` の固定HTTP client、payload-only compression、
  provenance/digest、機械的情報保持検査。Humanの明示接続指示により、固定endpointと
  `semantic-dense-v1` profileを利用可能にしたが、独立Compression Service本体は別deploy
  境界である。live smokeはtoken未設定のため`NOT_VERIFIED`である。
- **NOT VERIFIED / NOT CONNECTED**: Compression live availability、wire MCP transport、
  MCP planner proposal/apply authority、simulated-paid runtime E2E、Reviewer adapter、
  有限multi-cycleは未検証または未接続である。CompressionはG6O1-SIM/LIVEのbilling/evidence
  へ接続しない。
- 後続slice: wire transport、simulated-paid接続、Reviewer shadow、Control Planeが所有する
  有限cycle拡張。ただし各機構の既存authorityを二重化しない。
- G6O1-SIMは仕様分離済みとして扱い、Gate全体やG6O1-LIVEを自動昇格しない。
