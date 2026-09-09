# 02 Architecture, Intelligence, and Task

要件範囲: 3-6

## 3. 基本アーキテクチャ

dev_agentの論理構造は次のControl Planeとする。

~~~
                    dev_agent Control Plane
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                 │
          ▼                 ▼                 ▼
 Deterministic       Model Resource       Agent Backend
   Workflow              Pool                Pool
     L0              L1 / L2 / L3           Codex等
          │                 │                 │
          └─────────── Evaluator ─────────────┘
                            │
                     Durable State
                            │
                Resource / Budget / Audit
~~~

## 4. 知能階層

### INT-001 L0 — Deterministic Mechanism

LLMを必要としない処理は可能な限りL0へ置く。対象はvalidation、schema処理、formatter、lint、build、unit test、grep/search、file operation、diff、state transition、quota calculation、retry/backoff、cache、deterministic transformation、確立済みWorkflow。

L0で可能な処理を利便性だけでLLMへ委任しない。

### INT-002 L1 — Cheap Worker

無料または極低コストの軽量Cloud Modelを主対象とし、狭いcode patch、test追加、extraction、classification、structured transformation、文書同期、定型修正、小規模生成、大量の独立Taskを担当する。入力、出力、Acceptanceを狭く定義する。

Model能力を下げる場合は、同時にTask自由度も下げる。

### INT-003 L2 — Core Reasoner

Task分解、実行方針、Worker選択、Provider選択要求、結果統合、異常判定、escalation判断、中規模設計、不確実な問題分析を担当する。特定Model名へ固定せず role = core_reasoning を満たすResourceから動的に選択する。

### INT-004 L3 — Expert / Auditor

高性能・高Resource消費Modelを、同一Taskの複数失敗、重大なModel間不一致、architecture変更、protected boundary変更、security-sensitive判断、原因不明regression、Self-Repair promotion、高リスクmerge reviewなど必要時だけ利用する。通常Workerにはしない。

## 5. ModelはRoleと分離

### INT-005 Role ≠ Model

CoreBrain、Developer、Reviewerを特定Provider/Modelへ恒久固定しない。Roleを要求能力として定義し、その時点で利用可能なResourceから選択する。

## 6. Task分類

### TASK-001 Task Type

最低限、deterministic、worker、reasoning、expert、delegated_agent、recovery、protectedへ分類可能にする。Task TypeはLLM単独判断で権限昇格できない。

### TASK-002 Minimum Sufficient Intelligence

Routerは最も賢いModelではなく、Task要求を満たす最小能力Classを選ぶ。基本順序は、要求能力、privacy、availability、quota、free/paid、expected success、latency/congestionとする。

## 現行実装への適用境界

Phase 7A/Bの移行境界として、`Task`は `task_type`、`risk`、
`required_capabilities` をtyped profileとして保持し、既存JSON payloadへ
後方互換に保存する。`TaskIntelligencePolicy`はこのprofileからL0〜L3の
minimum/maximum/allowed tierを決定的に算出し、Controllerはその結果を
ModelRequest metadataとaudit-visible requestへ渡す。明示opt-in時はresource
metadataのtierとexact matchしてrouteを制約するが、通常routingは変更せず、
モデルが自己申告で昇格できないpolicy seamを維持する。Phase 7Dでは、host側の
Evaluator結果をdurable eventへ記録し、有限なescalation planを返すcoordinator
まで追加した。planの明示review（accepted/rejected）と、受理済みplanを
dispatch-ready handoffとしてdurable記録する境界も実装済みだが、review後の
dispatch実行、workflow promotion、AgentBackend/MCPは後段である。
