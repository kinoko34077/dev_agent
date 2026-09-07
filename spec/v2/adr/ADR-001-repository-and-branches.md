# ADR-001: 同一 repository と v1 保全 branch

- Status: accepted
- Decision: v1 の Git history を保持したまま、`legacy/v1-final` と `v2/bootstrap` を分離する。
- Reason: 失敗資料と回帰 fixture の追跡性を残し、main への直接 rewrite を防ぐ。
- Consequence: v2 は別 namespace に置き、v1 Runtime を import しない。
