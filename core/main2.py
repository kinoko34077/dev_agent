# core/main.py

import os
from api.api_router import process_input
from utils.logger import setup_logger

def main():
    os.makedirs("logs", exist_ok=True)
    setup_logger(mode="timestamp", force=True)

    print("=== 🤖 dev_agent 起動 ===")
    print("ヒント: 'exit' または 'quit' で終了します。")

    while True:
        try:
            user_input = input("\n🗣 あなた > ").strip()
            if user_input.lower() in {"exit", "quit"}:
                print("👋 dev_agent 終了します。")
                break

            response = process_input(user_input)
            print(f"\n🤖 dev_agent > {response}")

        except KeyboardInterrupt:
            print("\n⚠️ 強制終了されました。")
            break
        except Exception as e:
            print(f"\n❌ 処理中にエラーが発生しました: {e}")

if __name__ == "__main__":
    main()
