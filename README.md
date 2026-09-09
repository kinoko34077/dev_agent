# 📦 dev_agent

> ## V2 DEVELOPMENT BRANCH
>
> この `v2/bootstrap` は新しい Provider-neutral Kernel の開発ブランチです。v2 の起点・実装状態は [`docs/V2_EXECUTION_PLAN.md`](docs/V2_EXECUTION_PLAN.md) と [`spec/v2/INTEGRATION_HARDENING.md`](spec/v2/INTEGRATION_HARDENING.md) を正とします。以下の Gemini / `core/main.py` 手順は legacy v1 の記録であり、v2 Runtime の起動手順ではありません。

このブランチでの現在の実行境界は `src/dev_agent`、v2 の運用設定は [`config/v2.yaml`](config/v2.yaml)、復旧操作は `python -m recovery.rescue diagnose --json` です。Phase 6 foundation は検証済みで、G6O2〜G6O6のoperational Gateも各責務内で検証済みです。G6O1のみ、有償Providerのworst-case実証とdeployment-owned budget設定の外部保護確認待ちです。Phase 6AではResourceLedger schema v6のquota domain/observation、quota-aware routing、正規化quota telemetry取り込み、Groq・Cloudflare・Mistral・OpenRouter Freeの注入transport Adapter契約を追加しています。Groq／Cloudflareにはopt-inの実HTTP Adapterも追加しましたが、資格情報なしのlive qualificationは`blocked_external`です。Phase 7A/BではTask profileと決定的なIntelligence Policy境界を追加し、実Providerのmodel-tier選択はまだ行いません。

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

次段階の要件は requirements Index (docs/requirements/README.md) から参照してください。Multi-Free ProviderのPhase 6A基盤のうち、quota identity/observation、operational resource observation、fresh quota routing、正規化quota telemetry取り込み、注入transport Adapter契約を実装済みです。Task profileとbounded intelligence policyはPhase 7A/Bの最初の境界として実装済みです。live Provider qualification、Provider固有header解析、実Providerのtier routing、Evaluator、AgentBackend/MCPは後段です。

通常のProvider経路は Controller -> ProviderDispatcher -> ProviderRegistry -> concrete Provider です。Controller内のdirect経路は既存呼び出し向けのcompatibility/legacy pathとして維持しています。Phase 7A/B境界、実HTTP Adapter、開発Worker Farm契約、Provider tool-call transcriptを含むローカル全テストは 288 passed, 1 skipped です。開発補助Workerのmanifest／result／worktree境界と組み込み保護領域は [`docs/DEVFARM.md`](docs/DEVFARM.md) を参照してください。

資格情報を外部環境へ設定できる場合のlive qualification入口は、`scripts/qualify_free_provider.py`です。Groq／Cloudflareの実証結果は、canonical Dispatcher経路・quota観測の有無・ToolCall往復を含むJSON artifactとして出力します。資格情報未設定時は`blocked_external`で終了し、Gateを自動昇格しません。

## legacy v1（履歴）

## 🧠 概要

`dev_agent` は、Google Gemini などの LLM API を用いた **自律型エージェント**の開発プロジェクトです。

> **v2 の開発を開始する際の通常の参照点:** [v2 実行計画](docs/V2_EXECUTION_PLAN.md)
>
> 現在のコードは v1 の履歴・障害分析資料として保持します。v2 は v1 を直接改修せず、Provider 非依存・有限実行・明示状態・外部復旧を備えた別の Kernel として段階的に構築します。

- **自己思考**（プロンプト生成・履歴保持）
- **自己実行**（関数・スクリプト実行）
- **自己修正**（再帰的実行・エラー補正）
- **自己拡張**（LLMの切替・記憶の統合）

を段階的に実現する設計思想に基づいて構築されています。

## 内的対話システム

エージェントは、自己改善と内省的な対話を可能にする内的対話システムを備えています。このシステムにより、エージェントは自身の応答や行動を客観的に分析し、より良い応答方法を模索することができます。

### 主な機能

- **内的対話の開始**: 特定のトピックについて自己分析を開始
- **対話の継続**: エージェントの応答に対する分析と改善提案
- **対話履歴の管理**: 最大履歴数の制限と要約機能
- **設定可能なパラメータ**: 温度、履歴数、モデルなどのカスタマイズ

### 使用例

```python
# 内的対話を開始
result = start_internal_dialogue("応答方法の改善")

# 対話を継続
analysis = continue_internal_dialogue("エージェントの応答")
```

### 設定

`config/config.yaml`で以下の設定が可能です：

```yaml
internal_dialogue:
  enabled: true
  max_history: 3
  temperature: 0.7
  model: "gpt-4"
  roles:
    internal: "recur"
    agent: "assistant"
```

---

## 🔧 環境要件

- Python 3.10 以上
- [Google Generative AI](https://ai.google.dev) アカウント（APIキー必須）
- ライブラリ:
  - `google-generativeai`
  - `pyyaml`
  - `python-dotenv`（`.env`使用時）

---

## 📁 ディレクトリ構成（抜粋）

```

dev\_agent/
├── core/                  # エージェント中核ロジック（main, executor 等）
├── api/                   # 各種LLM APIクライアント（Gemini等）
├── memory/                # 入出力・要約・初期化スクリプト
├── utils/                 # 補助ツール（logger 等）
├── templates/             # ベースプロンプト群
├── logs/                  # 実行時ログ保存
├── config/                # 設定ファイル（YAML形式）
└── main.py                # 起動エントリーポイント

````

---

## 🚀 セットアップ手順

```bash
# 仮想環境の作成・有効化
python -m venv venv
source venv/bin/activate  # Windowsなら venv\Scripts\activate

# 依存ライブラリのインストール
pip install -r requirements.txt
````

---

## 🔑 .env ファイルの作成

プロジェクトルートに `.env` ファイルを作成し、以下のように記述：

```
GEMINI_API_KEY=your-google-api-key-here
```

※ `.env` は `.gitignore` により Git に上がらないよう管理されています。

---

## ▶️ 起動方法

```bash
# エージェント起動
python core/main.py
```

起動後、標準出力および `logs/` にログファイルが生成されます。

---

## ⚠️ 注意事項

* `.env`, `venv/`, `__pycache__/`, `.log` などは必ず `.gitignore` に追加済みであること
* GeminiのAPI利用には**使用量制限**があるため、再帰処理や大規模プロンプトに注意
* 実行関数（run\_script等）は `sandbox_path` 配下に制限され、安全性に配慮

---

## 📌 拡張予定（ロードマップ概要）

* `api_router.py`: Gemini/GPT/Claudeなどをプロンプト内容で自動切替
* `scheduler.py`: タスク定期実行・失敗時再試行管理
* `web_ui/`: 履歴・状態・実行コントロールの視覚化
* `test/`: pytestによるユニットテスト追加

---

## 👤 開発者

KiNoTch.（2025）

> 自由と利益のための自律知性体を設計中。

## ✅ 主な改善点

| 項目 | 内容 |
|------|------|
| **概要に流れを反映** | 自己思考→自己修正までのループを明示 |
| **ディレクトリ説明追加** | `core/`, `api/`, `memory/` などの役割を把握しやすく |
| **.env手順と注意分離** | セキュリティとGit管理の観点から切り分け強調 |
| **ロードマップ宣言** | 将来的拡張への布石として導線を提示 |
