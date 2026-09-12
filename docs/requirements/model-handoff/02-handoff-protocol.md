# Handoff Protocol v0.1

実装は `src/dev_agent/handoff/protocol.py` が正本である。

## HandoffEnvelope

必須情報は次のとおり。

- `handoff_id`
- `kind`
- `subject`
- `instruction`
- `conditions[]`
- `cautions[]`
- `source_role`
- `target_role`
- `payload`
- `payload_mode`
- `payload_reference`（reference modeでは必須）

`requirements[]` と `metadata` は補助情報であり、既存authorityを発行しない。
`kind`は既知のkindを便利に使えるが、将来拡張を妨げない文字列境界とする。
モデル名をroleとして埋め込まず、`planner`、`reviewer`、`executor`、
`compression`等のroleとbackend/providerを分離する。

## Payload mode

- `original`: payload本文をそのまま保持する。
- `compressed`: 圧縮本文とoriginal reference/digest/provenanceを保持する。
- `reference`: 再取得可能なrepository、commit、artifact等を参照し、本文を
  コピーしない。

compressed payloadはlossy transportであり、originalの代替SSOTではない。
下流が判断できない場合は`original_reference`へ戻る。

## Renderer

`render_handoff(..., renderer="kinotch-ja-v1")`だけが、従来の
「以下、…／ただし、…／…に注意すること。／┈┈┈」形式へ変換する。
自然言語render結果自体はSSOTではない。

初期sliceはpure typed contractであり、独立したdurable state machineや
Schedulerを追加しない。durableな実行結果は既存Task/Event/DevFarm artifact
境界へ、後続の1-cycle compositionで接続する。

## Role boundary and one cycle

`PlannerRole`、`ExecutorRole`、`ReviewerRole` はmodel名を知らないProtocolで
ある。development-onlyの `scripts/handoff_cycle.py` がこれらを一度だけ接続し、
ReviewerからHumanへ戻った時点で停止する。Executorの実作業は既存のDevFarm、
isolated worktree、Host Verificationを再利用し、handoff層がSchedulerやTask
stateを所有しない。
