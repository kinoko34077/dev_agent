# dev_agent v2 Requirements

このディレクトリは、v2の要件を目的別の小さな章へ分割して保持する。

## 現行の要件

- [Multi-Free Provider / Intelligence Hierarchy / External Agent Integration](multi-free-provider/00-index.md)
- [Autonomous Hierarchy / Cross-cutting Hardening Audit](autonomous-hierarchy-hardening/00-index.md)

「Autonomous Hierarchy / Cross-cutting Hardening Audit」は、既存の
Multi-Free Provider要件を置き換えず、Phase 7後半へ進む前の横断hardening作業を
分離して記録する実装入力である。実装状態・検証結果・Gate判定の正本は、引き続き
`docs/CURRENT_STATE.md`、`spec/v2/GATE_STATUS.json`、`spec/v2/TRACEABILITY.md`
および各evidenceである。

## 読み方

まず章indexを読み、実装対象の章だけを開く。全体像が必要な場合だけ、indexから7章を順番に読む。

要件文書は実装コードの証跡ではない。実装済み・検証済み・外部待ちは、既存の spec/v2/GATE_STATUS.json とPhase文書を正本とする。
