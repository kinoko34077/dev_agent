# 📦 dev_agent

> ## V2 DEVELOPMENT BRANCH
>
> この `v2/bootstrap` は新しい Provider-neutral Kernel の開発ブランチです。v2 の起点・実装状態は [`docs/V2_EXECUTION_PLAN.md`](docs/V2_EXECUTION_PLAN.md) と [`spec/v2/INTEGRATION_HARDENING.md`](spec/v2/INTEGRATION_HARDENING.md) を正とします。以下の Gemini / `core/main.py` 手順は legacy v1 の記録であり、v2 Runtime の起動手順ではありません。

このブランチでの現在の実行境界は `src/dev_agent`、非secretなv2ポリシー参考ファイルは [`config/v2.yaml`](config/v2.yaml)、復旧操作は `python -m recovery.rescue diagnose --json` です。Operation runtimeは`config/v2.yaml`を自動読込みせず、実行時のResource／Budget設定は永続SQLiteと明示的なoperator／環境設定を正本とします。Phase 6 foundation とG6O2〜G6O6は検証済み、G6O1のみ有償Providerのworst-case実証とdeployment-owned budget設定の外部保護待ちです。Gemini 3.5 Flash-Lite（L1 Worker）とGemini 3.8 Flash（L2 core）は、thoughtSignatureを含むToolCall往復・Controller E2E・audit・budget reconciliationのlive qualification済みです。Cloudflare Workers AI、OpenRouter Free、Ollamaもqualified、GroqはHTTP 403、MistralはHTTP 429、SambaNovaはHTTP 429/402で未qualification/inactiveです。Phase 7A〜7Eではbounded tier routing、thinking effort分離、deterministic evaluator、host review、有限escalation dispatch、workflow proposal、明示的な有限Evaluator→dispatch→TaskLifecycle合成まで実装済みです。development-onlyのCommander親Planと既存DevFarmのplan／dispatch／collect／verify／resume入口、hard-filter限定のevidence-based advisory順位付け、ModelProviderと分離したthin AgentBackend contract、既存StateStoreへ接続するAgentBackend dispatcherも追加済みです。実Codex adapter／MCPは未実装です。詳細な現在状態は [`docs/CURRENT_STATE.md`](docs/CURRENT_STATE.md) を参照してください。

## v2 quickstart（現行）

`config/v2.yaml` は非secretなポリシー参考ファイルであり、Operation runtimeは自動読込みしません。実行時のResource／Budget設定は永続SQLiteと明示的なoperator／環境設定が正本です。

v2 の開発・検証では、legacy v1 の依存関係や起動経路を使用しません。

```bash
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-v2-dev.txt
python -m pytest tests/v2 -q
python -m scripts.check_gate
python -m recovery.rescue diagnose --root . --json
```

### v2 Operation Layer

人間向けの最小起動入口は、既存のQueue／WorkerRunner／Controller／ProviderDispatcherを
compositionする標準ライブラリCLIです。初期smokeは無課金の`fake` Providerを使うため、
実Providerを選ぶ場合だけ明示的に環境変数またはフラグを指定します。

```bash
python -m src.dev_agent submit "小さな検証Task"
python -m src.dev_agent start --once
python -m src.dev_agent status <task-id>
python -m src.dev_agent stop                 # foreground Worker loopへ停止要求
python -m src.dev_agent stop <task-id>       # 停止要求とTaskの安全なcancel
```

`start`はforegroundのbounded Worker loopです。`--once`は1件だけ処理して終了します。
StateStoreとQueueは同じSQLiteファイルを共有し、lease proofのtransactional検証を維持します。
実Providerの例は`DEV_AGENT_PROVIDER=gemini`と`DEV_AGENT_MODEL=gemini-3.5-flash-lite`です。
Operation Layerは自動activation、無承認の外部送信、自動mergeを行いません。

主要な現行コードは次の境界に分かれています。

```text
src/dev_agent/
├── runtime/      # Controller、checkpoint、RuntimeState
├── state/        # SQLite/JSON durable StateStore
├── providers/    # Provider-neutral contract、dispatch、audit
├── resources/    # ResourceLedger、Budget、router、survival
├── scheduler/    # durable queue、lease、worker
├── tools/        # schema、approval、process isolation
├── intelligence/ # Task profile、bounded intelligence policy
└── security/     # audit sanitizer、event artifact
```

Phase 6 の現在地と未完了の外部証跡は [`docs/CURRENT_STATE.md`](docs/CURRENT_STATE.md)、[`docs/PHASE6_PLAN.md`](docs/PHASE6_PLAN.md)、[`spec/v2/GATE_STATUS.json`](spec/v2/GATE_STATUS.json) を参照してください。Phase 7のEscalationExecutorはaccepted handoffを再検証してcanonical ProviderDispatcherへ有限dispatchします。Gemini 3.x qualificationとGemini L1 DevFarm Workerのhost verificationも同Current Stateへ記録しています。Evaluator→dispatch→TaskLifecycleの明示的な有限合成、development-only Commander親Plan、hard-filter限定のevidence-based advisory順位付けは実装済みですが、Evaluatorの自動循環・ResourceRouterへの実績Routing自動接続、AgentBackend/MCP、Self-Improvementは未完了です。Commanderの運用は [`docs/CODEX_COMMANDER.md`](docs/CODEX_COMMANDER.md) を参照してください。

以下は v1 の履歴・互換運用情報です。

## 次段階要件と移行前調整

次段階の要件は requirements Index (docs/requirements/README.md) から参照してください。Multi-Free ProviderのPhase 6A基盤、Provider共通HTTP層、quota identity/observation、generic unit/authority、fresh quota routing、Phase 7A〜7Eのbounded execution境界を実装済みです。Groq/Mistral/SambaNovaのqualificationは外部状態により未完了ですが、判定を推測しません。Evaluator→dispatch→TaskLifecycleの明示的な有限合成、development-only Commander親Plan、hard-filter限定のevidence-based advisory順位付け、ModelProviderと分離したthin AgentBackend contract、既存effect intentへ接続するAgentBackend dispatcherは実装済みですが、ResourceRouterへの自動接続、実Codex adapter、MCPは未完了です。

通常のProvider経路は Controller -> ProviderDispatcher -> ProviderRegistry -> concrete Provider です。Controller内のdirect経路は既存呼び出し向けのcompatibility/legacy pathとして維持し、intent／audit／replayは `LegacyDirectProviderJournal` に隔離しています。ProviderFactory、Gemini 3.x transcript、Phase 7A〜7E境界、Evaluator→dispatch cycle、有限Lifecycle合成、共通OpenAI互換HTTP Adapter、開発Worker Farm契約、host側metrics、generic quota、最小Operation Layer、development-only Commander親Plan、hard-filter限定のevidence-based advisory順位付け、thin AgentBackend contract、AgentBackend dispatcherを含むローカル全テストは `511 passed, 1 skipped` です。DevFarmのmanifest／result／worktree境界とhost verificationは [`docs/DEVFARM.md`](docs/DEVFARM.md) を参照してください。Commanderの親Plan運用は [`docs/CODEX_COMMANDER.md`](docs/CODEX_COMMANDER.md)、システム責務表は [`docs/SYSTEM_MAP.md`](docs/SYSTEM_MAP.md) を参照してください。Provider capabilityとlive qualificationの証跡は [`spec/v2/PROVIDER_CAPABILITY_MATRIX.json`](spec/v2/PROVIDER_CAPABILITY_MATRIX.json) と [`spec/v2/evidence/`](spec/v2/evidence/) を参照してください。リファクタ後の現在状態・CI証跡は [`docs/CURRENT_STATE.md`](docs/CURRENT_STATE.md) を正本とします。

資格情報を外部環境へ設定できる場合のfree-provider live qualification入口は、`scripts/qualify_free_provider.py`です。Gemini／Groq／Cloudflare／OpenRouter／Mistralの実証結果は、canonical Dispatcher経路・quota観測の有無・ToolCall往復を含むJSON artifactとして出力します。Gemini 3.5 Flash-Liteと3.8 Flashはqualification済み、Groqはmodels endpoint HTTP 403、Mistralはキー読込み後の推論HTTP 429で未 qualificationです。SambaNovaはHTTP Adapterと `/v1/models` 接続確認まで実装済みですが、推論はHTTP 429/402となったため無償Provider qualificationへは入れていません。資格情報未設定時は`blocked_external`で終了し、rate limitやその他の外部失敗もGateを自動昇格しません。Groqの現行診断証跡は [`spec/v2/evidence/groq-models-2026-09-09.json`](spec/v2/evidence/groq-models-2026-09-09.json)、SambaNovaの現行失敗証跡は [`spec/v2/evidence/phase6-sambanova-free-2026-09-09.json`](spec/v2/evidence/phase6-sambanova-free-2026-09-09.json) と [`spec/v2/evidence/phase6-sambanova-gpt-oss-120b-2026-09-09.json`](spec/v2/evidence/phase6-sambanova-gpt-oss-120b-2026-09-09.json)、Mistralの最新失敗証跡は [`spec/v2/evidence/phase7-mistral-2026-09-10.json`](spec/v2/evidence/phase7-mistral-2026-09-10.json)、Gemini 3.xの証跡は [`spec/v2/evidence/`](spec/v2/evidence/) です。

## legacy v1（履歴）

v1の実行資産と履歴は、凍結済みの `legacy/v1-final` branch と `requirements-v1-legacy.txt` に隔離しています。現行 `v2/bootstrap` の runtime、設定、テスト、復旧手順は本書上部と `docs/` / `spec/` を参照してください。
