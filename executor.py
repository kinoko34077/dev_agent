# executor.py

import yaml
import logging
from functions_registry import FUNCTIONS

class Executor:
    def __init__(self):
        logging.info("Executor初期化完了")

    def execute(self, actions_yaml: str):
        """
        【実行】ブロックを受け取り、YAML解析して登録された関数を呼び出す
        """
        if not actions_yaml:
            logging.info("実行するアクションなし")
            return

        try:
            actions = yaml.safe_load(actions_yaml)

            if not isinstance(actions, list):
                logging.error("【実行】ブロックがリスト形式ではありません")
                return

            for action in actions:
                for func_name, params in action.items():
                    self.run_function(func_name, params)

        except Exception as e:
            logging.error(f"アクション実行中のエラー: {str(e)}")
            raise

    def run_function(self, func_name: str, params: dict):
        """
        指定された関数名とパラメータで関数を実行
        """
        if func_name not in FUNCTIONS:
            logging.warning(f"未登録の関数が呼び出されました: {func_name}")
            return

        try:
            func = FUNCTIONS[func_name]
            if params:
                func(**params)
            else:
                func()

            logging.info(f"関数 {func_name} 実行成功")

        except Exception as e:
            logging.error(f"関数 {func_name} 実行失敗: {str(e)}")
