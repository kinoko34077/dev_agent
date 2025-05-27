# core/main.py

import os
import sys
import logging
from utils.helpers import safe_mkdir, setup_logger
from core.executor import Executor
from utils.output_manager import output_manager, OutputType

def main():
    # ログディレクトリの作成
    safe_mkdir("logs")
    setup_logger(mode="timestamp", force=True)
    
    # 起動ログ
    logging.info("=== dev_agent 起動 ===")
    output_manager.output("=== 🤖 dev_agent 起動 ===", OutputType.SYSTEM)
    output_manager.output("ヒント: 'exit' または 'quit' で終了します。", OutputType.SYSTEM)

    try:
        # Executorの初期化
        logging.info("Executorの初期化を開始")
        executor = Executor()
        logging.info("Executorの初期化が完了")

        while True:
            try:
                # ユーザー入力の受付
                user_input = input("\n🗣 あなた > ").strip()
                
                # 終了条件のチェック
                if user_input.lower() in {"exit", "quit"}:
                    logging.info("ユーザーによる終了要求")
                    output_manager.output("👋 dev_agent 終了します。", OutputType.SYSTEM)
                    break

                # 入力のログ記録
                logging.info(f"ユーザー入力: {user_input}")
                
                # Executorを使用して処理を実行
                try:
                    response = executor.execute(user_input)
                    # 応答のログ記録
                    logging.info(f"エージェント応答: {response}")
                    print(f"\n🤖 dev_agent > {response}")
                except Exception as e:
                    error_msg = f"処理実行中にエラーが発生しました: {str(e)}"
                    logging.error(f"Executor実行エラー: {str(e)}", exc_info=True)
                    output_manager.output(f"\n❌ {error_msg}", OutputType.ERROR)
                    print(f"\n❌ {error_msg}")

            except KeyboardInterrupt:
                logging.warning("キーボード割り込みによる強制終了")
                output_manager.output("\n⚠️ 強制終了されました。", OutputType.ERROR)
                break
            except Exception as e:
                error_msg = f"予期せぬエラーが発生しました: {str(e)}"
                logging.error(f"メインループ例外: {str(e)}", exc_info=True)
                output_manager.output(f"\n❌ {error_msg}", OutputType.ERROR)
                print(f"\n❌ {error_msg}")

    except Exception as e:
        error_msg = f"初期化エラー: {str(e)}"
        logging.error(error_msg, exc_info=True)
        output_manager.output(f"\n❌ {error_msg}", OutputType.ERROR)
        print(f"\n❌ {error_msg}")
        sys.exit(1)

if __name__ == "__main__":
    main()
