# dev_agent

## 概要

- 自律型エージェント開発プロジェクト
- Gemini APIを使用し、自己思考・自己実行・自己修正を目指す

## 環境要件

- Python 3.10以上
- google-generativeai
- pyyaml
- dotenv (optional)

## セットアップ手順

```bash
# 仮想環境作成
python -m venv venv
source venv/bin/activate

# ライブラリインストール
pip install -r requirements.txt
```

## 起動方法

```bash
python main.py
```

## 注意事項

- `.env`ファイルにAPIキーを記述し、Gitに上げないこと
- venv/, __pycache__/, .envは必ず.gitignoreに登録
