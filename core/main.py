# core/main.py

import os
import sys
import logging
from utils.logger import setup_logger
from api.client import LLMClient
from core.executor import Executor
from core.function_executor import execute_function_call
from utils.helpers import safe_mkdir
from core.functions_registry import recursion_flag


def main():
    safe_mkdir("logs")
    setup_logger(mode="timestamp", force=True)

    print("=== 🤖 dev_agent 起動 ===")
    print("ヒント: 'exit' または 'quit' で終了します。")

    llm_function = LLMClient(role="function")
    llm_thinker = LLMClient(role="thinker")
    executor = Executor()

    while True:
        try:
            user_input = input("\n🗣 あなた > ").strip()
            if user_input.lower() in {"exit", "quit"}:
                print("👋 dev_agent 終了します。")
                break

            # Step 1: 関数呼び出しとして試行
            function_call = llm_function.invoke(user_input)
            if function_call:
                result, error = execute_function_call(function_call)

                if error:
                    print(f"❌ 関数実行エラー: {error}")
                    continue

                # 関数実行結果を LLM に返却し、応答取得
                final_response = llm_function.respond_with_result(function_call.name, result)
                print(f"\n🤖 dev_agent > {final_response}")
            else:
                # Step 2: 通常出力
                response = llm_thinker.ask(user_input)
                print(f"\n🤖 dev_agent > {response}")

        except KeyboardInterrupt:
            print("\n⚠️ 強制終了されました。")
            break
        except Exception as e:
            print(f"\n❌ 処理中にエラーが発生しました: {e}")
            logging.error(f"mainループ例外: {str(e)}")


if __name__ == "__main__":
    main()
