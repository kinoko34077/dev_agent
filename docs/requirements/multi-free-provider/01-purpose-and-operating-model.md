# 01 Purpose and Operating Model

対象: dev_agent v2  
要件範囲: 1-2

## 1. 目的

dev_agentは単一の高性能LLMに依存する「賢いAgent」ではなく、必要な仕事に必要十分な知能・資源だけを割り当て、低コストかつ長時間継続的にTaskを処理する自律Control Planeとする。

最適化対象は一回の回答性能ではなく、次の比率とする。

~~~
長期間に完了できる有用Task総量
──────────────────────
消費Resource・費用・人間介入
~~~

## 2. 上位運用思想

### OBJ-001 継続稼働優先

瞬間的な最高性能のModelを常用せず、無料資源、軽量Model、中性能Model、Subscription allowance、高性能Model、Paid APIを段階的に利用する。総運用コストを抑えながら長時間稼働できることを優先する。

### OBJ-002 判断と実行の分離

現在の人間運用を上位設計モデルとして採用する。

~~~
通常Chat
→ GitHub監査・設計・考察
→ 指示書
→ Codex
→ 実装・Test
→ GitHub
→ 再監査
~~~

考える主体と大量実行する主体は分離してよい。高性能Reasoner自身が全作業を行う必要はない。

### OBJ-003 Mechanism-heavy / Intelligence-on-demand

可能な処理は、高性能AI、中性能AI、軽量AI、決定的Workflowの順で降格可能にする。同じ判断を繰り返しLLMへ再生成させない。

## 現行実装への適用境界

この章は運用目的を定める。現行移行前調整では、Intelligence TierやTask分類の実装を開始せず、既存Phase 6の決定的な実行・予算・監査境界を維持する。
