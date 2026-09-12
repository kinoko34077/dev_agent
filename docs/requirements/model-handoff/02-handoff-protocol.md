# Handoff Protocol v0.1 — Control semantics extension

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

`requirements[]`、optionalな`directive`、`metadata` はControl側の補助情報であり、
既存authorityを発行しない。
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

## `HandoffDirective`

`HandoffDirective`は後方互換なoptional Control valueである。Payload本文へ
instructionや制約を書き戻さず、次の意味をJSON-safeかつboundedに保持する。

- `exclusions[]`: 今回は行わないこと・範囲外。
- `focus[]`: 特に詳細に扱う対象。
- `payload_semantics`: audit/analysis/instruction/source/current-state/roadmap/
  user-decision/execution-evidenceのPayload意味。
- `comparison_targets[]` / `comparison_axes[]`: 現行とroadmap等の比較要求。
- `output_contract`: format、detail、ordering、include/exclude、section detailの
  最小presentation要求。
- `source_requirements[]` / `authority_source`: 現物を再取得すべきsourceと、
  現在問で正本にするsource。
- `continuation_mode`: `fresh`、`continue`、`recheck`、`reaudit_after_change`、
  `reanalyze_after_correction`、`integrate_previous`。

`reanalyze_after_correction`はlatest correction、`latest_user_correction` authority、
`prior_interpretation_invalidated=true`を同時に要求する。旧解釈と最新訂正を
平均・再統合しない。

Control内のsource precedenceは、衝突するユーザー仕様について現在の明示指示、
最新訂正、個別仕様、Domain Source、Core Source、関連過去context、一般既定値の
順を維持する。repository等の再取得可能な事実はreference/source requirementとして
扱い、過去会話や前段Modelの分析で置き換えない。

`current_state_request`、`current_state_analysis`、`roadmap_comparison`、
`reaudit_after_change`、`reanalyze_after_correction`、`integration_request`、
`implementation_instruction`、`critical_adjacent_audit`、`decision_request`、
`sequence_planning_request`はこのControlを組み立てる軽量presetである。これらは
repository取得、分析、実装、budget、approvalを所有しない。

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
stateを所有しない。`OneCycleDevelopmentLoop.run()`はHuman側の`directive`を
Planner requestへ渡せるが、ReviewerからPlannerへの自動次cycleは開始しない。
