# 📦 dev_agent

> ## V2 DEVELOPMENT BRANCH
>
> この `v2/bootstrap` は新しい Provider-neutral Kernel の開発ブランチです。v2 の起点・実装状態は [`docs/V2_EXECUTION_PLAN.md`](docs/V2_EXECUTION_PLAN.md) と [`spec/v2/INTEGRATION_HARDENING.md`](spec/v2/INTEGRATION_HARDENING.md) を正とします。以下の Gemini / `core/main.py` 手順は legacy v1 の記録であり、v2 Runtime の起動手順ではありません。

このブランチでの現在の実行境界は `src/dev_agent`、v2 の運用設定は [`config/v2.yaml`](config/v2.yaml)、復旧操作は `python -m recovery.rescue diagnose --json` です。Phase 6 foundation は検証済みで、G6O2〜G6O6のoperational Gateも各責務内で検証済みです。G6O1のみ、有償Providerのworst-case実証とdeployment-owned budget設定の外部保護確認待ちです。Phase 6AではResourceLedger schema v7のquota domain/generic observation、quota-aware routing、正規化quota telemetry取り込み、Groq・Cloudflare・Mistral・OpenRouter Free・SambaNovaのProvider-neutral Adapter境界を追加しています。Cloudflare Workers AIとOpenRouter Freeは現行Phase 6 canonical経路でlive qualification成功、Groqはmodels endpoint HTTP 403、SambaNovaはmodels endpoint HTTP 200後の推論HTTP 429/402で未 qualificationです。quota非報告時は`unknown_not_reported`のままです。Phase 7A/BではTask profileと決定的なIntelligence Policy境界を追加し、実Providerのmodel-tier選択はまだ行いません。

## v2 quickstart（現行）

v2 の開発・検証では、legacy v1 の依存関係や起動経路を使用しません。

```bash
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-v2-dev.txt
python -m pytest tests/v2 -q
python -m scripts.check_gate
python -m recovery.rescue diagnose --root . --json
```

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

Phase 6 の現在地と未完了の外部証跡は [`docs/PHASE6_PLAN.md`](docs/PHASE6_PLAN.md) と [`spec/v2/GATE_STATUS.json`](spec/v2/GATE_STATUS.json) を参照してください。Phase 7A/BのTask profile / policy seamは開始済みですが、Evaluator、escalation、実Providerのmodel-tier routing、AgentBackend/MCPは未着手です。

以下は v1 の履歴・互換運用情報です。

## 次段階要件と移行前調整

次段階の要件は requirements Index (docs/requirements/README.md) から参照してください。Multi-Free ProviderのPhase 6A基盤のうち、quota identity/observation、generic unit/authority、operational resource observation、fresh quota routing、正規化quota telemetry取り込み、注入transport Adapter契約を実装済みです。GroqとSambaNovaのrate-limit header正規化、CloudflareのNeuron消費推定もAdapter内へ閉じ込めていますが、Groq/Mistralのlive qualification、未実装Providerの固有quota観測、実Providerのtier routing、Evaluator、AgentBackend/MCPは後段です。

通常のProvider経路は Controller -> ProviderDispatcher -> ProviderRegistry -> concrete Provider です。Controller内のdirect経路は既存呼び出し向けのcompatibility/legacy pathとして維持しています。ProviderFactory、Phase 7A/B境界、共通OpenAI互換HTTP Adapter、開発Worker Farm契約、manifest-scoped Worker Runner、Provider tool-call transcript、generic quotaを含むローカル全テストは 315 passed, 1 skipped です。RoutingSnapshotによるresource/quota観測の一括読出しも導入済みです。開発補助Workerのmanifest／result／worktree境界と組み込み保護領域は [`docs/DEVFARM.md`](docs/DEVFARM.md) を参照してください。Cloudflare live qualificationの証跡は [`spec/v2/evidence/phase6-cloudflare-free-2026-09-09.json`](spec/v2/evidence/phase6-cloudflare-free-2026-09-09.json)、OpenRouter Freeの証跡は [`spec/v2/evidence/phase6-openrouter-free-2026-09-09.json`](spec/v2/evidence/phase6-openrouter-free-2026-09-09.json) を参照してください。

資格情報を外部環境へ設定できる場合のfree-provider live qualification入口は、`scripts/qualify_free_provider.py`です。Groq／Cloudflare／OpenRouter／Mistralの実証結果は、canonical Dispatcher経路・quota観測の有無・ToolCall往復を含むJSON artifactとして出力します。Groqはmodels endpoint HTTP 403、Mistralは現在キー未設定で、いずれも未 qualificationです。SambaNovaはHTTP Adapterと `/v1/models` 接続確認まで実装済みですが、推論はHTTP 429/402となったため無償Provider qualificationへは入れていません。資格情報未設定時は`blocked_external`で終了し、rate limitやその他の外部失敗もGateを自動昇格しません。Groqの現行診断証跡は [`spec/v2/evidence/groq-models-2026-09-09.json`](spec/v2/evidence/groq-models-2026-09-09.json)、SambaNovaの現行失敗証跡は [`spec/v2/evidence/phase6-sambanova-free-2026-09-09.json`](spec/v2/evidence/phase6-sambanova-free-2026-09-09.json) と [`spec/v2/evidence/phase6-sambanova-gpt-oss-120b-2026-09-09.json`](spec/v2/evidence/phase6-sambanova-gpt-oss-120b-2026-09-09.json) です。

## legacy v1（履歴）

v1の実行資産と履歴は、凍結済みの `legacy/v1-final` branch と `requirements-v1-legacy.txt` に隔離しています。現行 `v2/bootstrap` の runtime、設定、テスト、復旧手順は本書上部と `docs/` / `spec/` を参照してください。
