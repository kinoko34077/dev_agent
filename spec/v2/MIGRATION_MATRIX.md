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

## v2 internal refactor ownership

| 現行責務 | 正本位置 | 移行ルール |
| --- | --- | --- |
| Qualification load／exact identity／canonical capability projection | `src/dev_agent/resources/qualification.py` | runtime session内でCatalogを一度だけ構築し、expired／duplicate／invalid identityはfail-closed。global singletonやimport-time readは導入しない |
| Operation composition | `src/dev_agent/operation_bootstrap.py`, `operation_planning.py`, `cli.py` | `OperationService` facadeと外部CLI意味を維持し、内部componentを直接公開しない |
| Durable stop control | `src/dev_agent/state/control_repository.py` | Operation専用の第二StateStoreを作らず、既存SQLite SSOTへ接続する |
| Lease fencing primitive | `src/dev_agent/persistence/lease.py` | StateからScheduler concrete implementationを参照せず、atomic transaction semanticsを維持する |
| Resource schema／ordered migration | `src/dev_agent/resources/schema.py` | ResourceLedger facadeとschema versionを維持し、migration順序を変更しない |
| Unknown-quota local admission | `src/dev_agent/resources/quota_store.py` | quota telemetryと混同しないbounded local windowを同一SQLite lock/transactionで保持し、ResourceLedger facadeからのみ利用する |
| Package compatibility exports／legacy provider path | `src/dev_agent/{providers,intelligence,resources,backends,scheduler,state}/__init__.py`、`src/dev_agent/runtime/controller.py` | public import namesは維持し、未使用のadapter／legacy compatibility pathをcanonical importから遅延する。legacy path自体は削除しない |
| Architecture/preflight tooling | `scripts/check_architecture.py`, `scripts/test_scope.py` | read-only開発補助。runtime authority、Scheduler、StateStoreを所有しない |
| Provider billing / cloud binding identity | `src/dev_agent/resources/billing_catalog.py`, `src/dev_agent/providers/factory.py`, `src/dev_agent/operation.py` | `billing_mode`でfixed-freeとallowance/credit-backedを分離し、local `ollama`、`ollama_cloud`、`vercel`を別identityとして扱う。credential valueは定義・artifactへ保存しない |

この表はv1資産の復活を意味しない。v2の内部責務移動と互換facadeの所有者を記録するためのものとする。
