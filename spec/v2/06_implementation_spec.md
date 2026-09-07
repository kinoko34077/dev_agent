# 実装仕様（Phase 0）

候補ディレクトリは `src/dev_agent/{domain,runtime,state,tools,providers}`、独立復旧は `recovery/` とする。alpha0 の実装は標準ライブラリ中心で開始し、第三者ライブラリが必要になった時点で ADR を追加する。

禁止事項: v1 Executor / LLMClient / RecursionManager の import、main への直接 rewrite、無制限 retry / recursion、任意 Python import を sandbox と見なすこと。
