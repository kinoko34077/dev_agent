# core/recursion_manager.py

import time
import logging
import yaml

# 関数トリガー状態を共有する辞書（recursion_flag）をインポート
from core.functions_registry import recursion_flag

class RecursionManager:
    """
    【RecursionManagerクラス】
    - 自己ターン再帰（trigger_recursion）処理を制御
    - 外部からmain()関数を引数として受け取り、必要に応じて再帰呼び出し
    - 最大再帰回数や遅延時間はconfig.yamlから取得
    """

    def __init__(self, main_func):
        """
        コンストラクタ

        Args:
            main_func (callable): 再帰時に呼び出すメイン関数（通常はmain.main関数）
        """
        self.main_func = main_func

        # 設定ファイルの読み込み（再帰関連設定）
        with open("config/config.yaml", "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        self.max_recursions = config.get("recursion", {}).get("max_recursions", 3)
        self.recursion_delay = config.get("recursion", {}).get("recursion_delay", 2.0)

    def handle_recursion(self):
        """
        trigger_recursion() により再帰フラグが立っている場合、
        main関数を再帰的に呼び出す。
        最大回数に達したら打ち切り、遅延時間も設定で制御。
        """
        recursion_count = 0

        while recursion_count < self.max_recursions:
            if recursion_flag["triggered"]:
                logging.info(f"自己ターン再帰 {recursion_count + 1}/{self.max_recursions}回目 実行中...")

                # 再帰の前に指定秒数の遅延（負荷軽減 or 時間調整用）
                time.sleep(self.recursion_delay)

                try:
                    # 再帰フラグをリセット（次ターンで再度設定される可能性あり）
                    recursion_flag["triggered"] = False

                    # main() を再帰的に呼び出す（引数で recurse=True を明示）
                    self.main_func(recurse=True)

                    recursion_count += 1

                except Exception as e:
                    logging.error(f"再帰ターン中エラー発生: {str(e)}")
                    break
            else:
                break
