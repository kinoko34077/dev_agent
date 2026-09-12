# Model Handoff / Compression Requirements

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

## 実装段階

- 初期slice: `src/dev_agent/handoff/` のHandoffEnvelope、validation、
  `kinotch-ja-v1` renderer。
- 実装済みslice: development-onlyの`OneCycleDevelopmentLoop`が
  Human → Planner → 既存DevFarm/Codex attempt → Reviewer → Human境界で停止する。
- 実装済みslice: `src/dev_agent/compression/` の固定HTTP client、payload-only compression、
  provenance/digest、機械的情報保持検査。独立Compression Service本体は別deploy境界。
- 後続slice: 必要時のsimulated-paid接続と、Control Planeが所有する有限cycle拡張。
- G6O1-SIMは仕様分離済みとして扱い、Gate全体やG6O1-LIVEを自動昇格しない。
