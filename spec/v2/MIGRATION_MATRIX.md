# v1 → v2 移行マトリクス

| v1 資産 | v2 の扱い | 境界 / 備考 |
| --- | --- | --- |
| Git history、logs、memory 入出力 | fixture / evidence | 失敗再現・golden task に抽出。秘密情報を除外 |
| Function registry metadata | concept only | `ToolSpec` として再設計 |
| access.yaml / secure_check | concept only | canonical path と capability policy に再実装 |
| Function manager / output classification | concept only | lifecycle / typed Event に分離 |
| `core/executor.py` | archive / no import | 責務過多・旧 protocol の証拠 |
| `api/client.py` | adapter reference only | `.text` / tuple ambiguity を持ち込まない |
| recursion manager / global recursion flag | removed from v2 | iterative Controller + Task Graph |
| internal dialogue | workflow concept only | Critic / Evaluator として後続実装 |
| dynamic `function_loader.py` import | prohibited | isolated Tool Runtime が代替 |
| `memory/memory_manager.py` | fixture / concept only | knowledge / state / event / artifact に分割 |
