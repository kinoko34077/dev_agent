# 📦 dev_agent

## 🧠 概要

`dev_agent` は、Google Gemini などの LLM API を用いた **自律型エージェント**の開発プロジェクトです。

- **自己思考**（プロンプト生成・履歴保持）
- **自己実行**（関数・スクリプト実行）
- **自己修正**（再帰的実行・エラー補正）
- **自己拡張**（LLMの切替・記憶の統合）

を段階的に実現する設計思想に基づいて構築されています。

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

```

---

## ✅ 主な改善点

| 項目 | 内容 |
|------|------|
| **概要に流れを反映** | 自己思考→自己修正までのループを明示 |
| **ディレクトリ説明追加** | `core/`, `api/`, `memory/` などの役割を把握しやすく |
| **.env手順と注意分離** | セキュリティとGit管理の観点から切り分け強調 |
| **ロードマップ宣言** | 将来的拡張への布石として導線を提示 |
