# core/main.py

import sys
import time
import os
import logging

# モジュール構造に合わせてインポート調整
#from api.api_client import GeminiClient
from memory.memory_manager import MemoryManager
from core.executor import Executor
from core.recursion_manager import RecursionManager
from core.functions_registry import recursion_flag
from utils.logger import setup_logger

# ----------------------------------------
# main.py
# dev_agent の実行エントリーポイント
# - 入力受付（ユーザーまたは再帰）
# - GPT応答処理（Gemini）
# - 【実行】ブロック処理（Executor）
# - メモリ記録・要約生成
# - 再帰制御（RecursionManager）
# ----------------------------------------

# ログ出力設定（logs/system.log への出力と標準出力の両立）
setup_logger(mode="static")      # logs/system.log に出力
# setup_logger(mode="timestamp")  # logs/YYMMDD_HHMM_system.log に出力

# 再帰マネージャーを格納（関数渡しのためグローバル）
recursion_manager = None


def main(recurse: bool = False):
    """
    dev_agent のメイン実行関数。
    ユーザー入力もしくは自己再帰入力を起点に、Geminiへのプロンプト送信→応答解析→関数実行→履歴保存を行う。

    Args:
        recurse (bool): True の場合、自己ターン継続として動作する（user_inputを省略）
    """
    from api.api_client import GeminiClient  # ← 関数内遅延importにする
    global recursion_manager

    # 各モジュール初期化
    gemini = GeminiClient()
    memory = MemoryManager()
    executor = Executor()

    # 初回起動時メッセージ
    if not recurse:
        print("エージェント起動完了。指示を入力してください。(終了するには 'exit')")

    while True:
        # 通常入力か再帰かで分岐
        if not recurse:
            user_input = input("\nあなたの指示> ").strip()
            if user_input.lower() == "exit":
                print("終了します。")
                logging.shutdown()
                sys.exit()
        else:
            user_input = "（自己ターン続行）"

        # プロンプト構築（直近履歴込み）
        prompt = memory.build_prompt(user_input)

        # Geminiへプロンプト送信・応答受信
        response = gemini.ask(prompt)
        content, actions = gemini.parse_response(response)

        # 出力表示（ユーザーへの応答）
        print(f"\nエージェント> \n{content}")

        # 【実行】ブロック処理（YAMLやPython構文の関数実行）
        if actions:
            executor.execute(actions)

        # 履歴保存と要約更新
        memory.update(user_input, content, actions)
        memory.save_summary(f"ダミー要約: {user_input} に対する応答")

        # 再帰処理がトリガされたか確認
        if recursion_flag["triggered"]:
            recursion_flag["triggered"] = False
            recursion_manager.handle_recursion()
            break

        # 自己ターンは1回限りで終了
        if recurse:
            break


if __name__ == "__main__":
    # 再帰マネージャに main 関数を渡す（自己再帰で呼び戻す用）
    recursion_manager = RecursionManager(main)
    main()
