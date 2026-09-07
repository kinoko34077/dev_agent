# 📦 dev_agent

> ## V2 DEVELOPMENT BRANCH
>
> この `v2/bootstrap` は新しい Provider-neutral Kernel の開発ブランチです。v2 の起点・実装状態は [`docs/V2_EXECUTION_PLAN.md`](docs/V2_EXECUTION_PLAN.md) と [`spec/v2/INTEGRATION_HARDENING.md`](spec/v2/INTEGRATION_HARDENING.md) を正とします。以下の Gemini / `core/main.py` 手順は legacy v1 の記録であり、v2 Runtime の起動手順ではありません。

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
