# main.py

import sys
import time
import os
import logging

from api_client import GeminiClient
from memory_manager import MemoryManager
from executor import Executor
from recursion_manager import RecursionManager
from functions_registry import recursion_flag

# ログ設定
logs_dir = os.path.join(os.getcwd(), "logs")
os.makedirs(logs_dir, exist_ok=True)

log_file_path = os.path.join(logs_dir, "system.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(log_file_path, encoding="utf-8"),
        logging.StreamHandler()
    ]
)

# ★ ここでは一旦仮置き、main()を渡すのは後ろで
recursion_manager = None

def main(recurse=False):
    global recursion_manager

    gemini = GeminiClient()
    memory = MemoryManager()
    executor = Executor()

    if not recurse:
        print("エージェント起動完了。指示を入力してください。(終了するには 'exit')")

    while True:
        if not recurse:
            user_input = input("\nあなたの指示> ").strip()
            if user_input.lower() == "exit":
                print("終了します。")
                logging.shutdown()
                sys.exit()
        else:
            user_input = "（自己ターン続行）"

        prompt = memory.build_prompt(user_input)
        response = gemini.ask(prompt)
        content, actions = gemini.parse_response(response)

        print(f"\nエージェント> \n{content}")

        if actions:
            executor.execute(actions)

        memory.update(user_input, content, actions)
        memory.save_summary(f"ダミー要約: {user_input} に対する応答")

        if recursion_flag["triggered"]:
            recursion_flag["triggered"] = False
            recursion_manager.handle_recursion()
            break

        if recurse:
            break

if __name__ == "__main__":
    recursion_manager = RecursionManager(main)  # ←ここでmain関数を渡す！
    main()
