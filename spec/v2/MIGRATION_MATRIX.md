# v1 → v2 移行マトリクス

| v1 資産 | v2 の扱い | 境界 / 備考 |
| --- | --- | --- |
| Git history、旧logs、旧memory 入出力 | legacy branch / minimal fixture | 完全なv1資産は `legacy/v1-final` に保全し、v2 treeには秘密情報を含まない最小fixture / evidenceだけを残す |
| Function registry metadata | concept only | `ToolSpec` として再設計 |
| access.yaml / secure_check | concept only | canonical path と capability policy に再実装 |
| Function manager / output classification | concept only | lifecycle / typed Event に分離 |
| `core/executor.py` | legacy branch only / no import | 責務過多・旧 protocol の証拠 |
| `api/client.py` | legacy branch only / adapter reference | `.text` / tuple ambiguity を持ち込まない |
| recursion manager / global recursion flag | removed from v2 | iterative Controller + Task Graph |
| internal dialogue | workflow concept only | Critic / Evaluator として後続実装 |
| dynamic `function_loader.py` import | prohibited | isolated Tool Runtime が代替 |
| `memory/memory_manager.py` | legacy branch only / fixture concept | knowledge / state / event / artifact に分割 |
