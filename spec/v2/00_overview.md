# v2 仕様骨格

これは Phase 0 の仕様ベースラインである。詳細を決めていない項目は candidate / deferred のままにし、実装都合で accepted に昇格させない。

## 目的

Provider を交換可能な資源として扱い、有限実行・明示状態・監査・復旧を決定的な Runtime が所有する。Agent は bounded な提案だけを返す。

## 初期マイルストーン

`v2-kernel-alpha0`: FakeProvider が一つの無害 ToolCall を返し、検証・実行・イベント保存・最終応答・完了状態までをネットワークなしで通す。

## 文書の読み方

`INVARIANTS.md` が不変条件、`01`〜`07` が要求・挙動・データ・API・実装・テスト、`TRACEABILITY.md` が ID の対応表、`MIGRATION_MATRIX.md` が v1 資産の扱いを定義する。判断が必要な技術選択は `adr/` に置く。
