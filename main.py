# main.py

import sys
from api_client import GeminiClient
from memory_manager import MemoryManager
from executor import Executor

def main():
    gemini = GeminiClient()
    memory = MemoryManager()
    executor = Executor()

    print("エージェント起動完了。指示を入力してください。(終了するには 'exit')")

    while True:
        user_input = input("\nあなたの指示> ").strip()
        if user_input.lower() == "exit":
            print("終了します。")
            sys.exit()

        # 履歴と現在の指示を組み合わせたプロンプト生成
        prompt = memory.build_prompt(user_input)

        # Geminiへ送信し、応答取得
        response = gemini.ask(prompt)

        # 応答内容を本文・実行枠に分離
        content, actions = gemini.parse_response(response)

        # 本文出力
        print(f"\n【本文】\n{content}")

        # 実行枠が存在すれば実行
        if actions:
            executor.execute(actions)

        # メモリ更新
        memory.update(user_input, content, actions)

if __name__ == "__main__":
    main()
