# main.py

import sys
from api_client import GeminiClient
from memory_manager import MemoryManager
from executor import Executor

import logging
import os

# ここでログ設定を一括初期化！
logs_dir = os.path.join(os.getcwd(), "logs")
os.makedirs(logs_dir, exist_ok=True)

log_file_path = os.path.join(logs_dir, "system.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(log_file_path, encoding="utf-8"),
        logging.StreamHandler()  # コンソールにも出力
    ]
)

# これでmain.py以降、どのファイルからlogging.info()を呼んでも必ずlogs/system.logに記録される！


def main():
    gemini = GeminiClient()
    memory = MemoryManager()
    executor = Executor()

    print("エージェント起動完了。指示を入力してください。(終了するには 'exit')")

    while True:
        user_input = input("\nあなたの指示> ").strip()
        if user_input.lower() == "exit":
            print("終了します。")
            import logging
            logging.shutdown()  # 追加：ログを確実にflushする
            sys.exit()

        # 履歴と現在の指示を組み合わせたプロンプト生成
        prompt = memory.build_prompt(user_input)

        # Geminiへ送信し、応答取得
        response = gemini.ask(prompt)

        # 応答内容を本文・実行枠に分離
        content, actions = gemini.parse_response(response)

        # 本文出力
        print(f"\nエージェント> \n{content}")

        # 実行枠が存在すれば実行
        if actions:
            executor.execute(actions)

        # メモリ更新
        memory.update(user_input, content, actions)


if __name__ == "__main__":
    main()
