# recursion_manager.py

import time
import logging
import yaml
from functions_registry import recursion_flag

class RecursionManager:
    def __init__(self, main_func):
        self.main_func = main_func  # 再帰時に呼び出すメイン処理（main.pyのmain()）

        # config読み込み
        with open("config.yaml", "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        self.max_recursions = config.get("recursion", {}).get("max_recursions", 3)
        self.recursion_delay = config.get("recursion", {}).get("recursion_delay", 2.0)

    def handle_recursion(self):
        """
        自己ターン続行トリガが立っていた場合、再帰実行する
        """
        recursion_count = 0

        while recursion_count < self.max_recursions:
            if recursion_flag["triggered"]:
                logging.info(f"自己ターン再帰 {recursion_count + 1}/{self.max_recursions}回目 実行中...")
                time.sleep(self.recursion_delay)

                try:
                    recursion_flag["triggered"] = False  # 再帰直前にリセット（次ターンで再設定可）
                    self.main_func(recurse=True)
                    recursion_count += 1
                except Exception as e:
                    logging.error(f"再帰ターン中エラー発生: {str(e)}")
                    break

            else:
                break
