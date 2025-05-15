# memory_context_initializer.py

import os
import json
import datetime
import logging

# ----------------------------------------
# メモリ初期化ユーティリティ
# - memory/context/配下の重要ファイル群を生成
# - 既に存在する場合はスキップ
# - CLIスクリプトとしても実行可能
# ----------------------------------------

# ログ出力の基本設定（ファイルではなくコンソール用）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# コンテキストファイルの格納ディレクトリ
MEMORY_CONTEXT_DIR = os.path.join("memory", "context")

# 作成対象ファイルとその内容（初期状態）
FILES_TO_CREATE = {
    "system_prompt.txt": "あなたはKiNoTch.の自律エージェントです。常にユーザー利益を最優先とし、状況に応じた最適な行動を設計・実行してください。",

    "config_snapshot.json": {
        "model_name": "gemini-1.5-pro",
        "temperature": 0.7,
        "system_prompt_version": "v1.0",
        "created_at": datetime.datetime.utcnow().isoformat()
    },

    "summary_combined.txt": "",

    "memory_meta.json": {
        "turns_completed": 0,
        "last_summary_generated": datetime.datetime.utcnow().isoformat(),
        "memory_version": "v1.0"
    }
}


def initialize_memory_context():
    """
    memory/context/ 以下に必要な初期ファイルを生成。
    既に存在する場合は上書きせずスキップする。
    """
    os.makedirs(MEMORY_CONTEXT_DIR, exist_ok=True)

    for filename, content in FILES_TO_CREATE.items():
        filepath = os.path.join(MEMORY_CONTEXT_DIR, filename)

        try:
            if not os.path.exists(filepath):
                if isinstance(content, dict):
                    # JSONファイルとして保存
                    with open(filepath, "w", encoding="utf-8") as f:
                        json.dump(content, f, indent=2, ensure_ascii=False)
                else:
                    # テキストファイルとして保存
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(content)

                logging.info(f"{filename} を初期化しました。")
            else:
                logging.info(f"{filename} は既に存在しています。スキップします。")

        except Exception as e:
            logging.error(f"{filename} の作成に失敗しました: {str(e)}")


# CLIスクリプトとして直接実行された場合
if __name__ == "__main__":
    initialize_memory_context()
    print("✅ memory/context/ 初期化完了")
