import os
import json
import datetime
import logging

# ログ設定（見えるように）
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

MEMORY_CONTEXT_DIR = os.path.join("memory", "context")

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
    os.makedirs(MEMORY_CONTEXT_DIR, exist_ok=True)

    for filename, content in FILES_TO_CREATE.items():
        filepath = os.path.join(MEMORY_CONTEXT_DIR, filename)
        try:
            # ファイルが存在しない場合のみ作成
            if not os.path.exists(filepath):
                if isinstance(content, dict):
                    with open(filepath, "w", encoding="utf-8") as f:
                        json.dump(content, f, indent=2, ensure_ascii=False)
                else:
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(content)
                logging.info(f"{filename} を初期化しました。")
            else:
                logging.info(f"{filename} は既に存在しています。スキップします。")
        except Exception as e:
            logging.error(f"{filename} の作成に失敗しました: {str(e)}")

if __name__ == "__main__":
    initialize_memory_context()
    print("✅ memory/context/ 初期化完了")
