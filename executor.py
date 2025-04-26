# executor.py

import ast
import logging
from functions_registry import FUNCTIONS

class Executor:
    def __init__(self):
        logging.info("Executor初期化完了")

    def execute(self, actions_code: str):
        """
        【実行】ブロックからPython呼び出し式を解析・実行
        """
        if not actions_code:
            logging.info("実行アクションなし")
            return

        try:
            # アクションコードの整形
            code = actions_code.strip()

            # Python構文解析
            tree = ast.parse(code, mode="exec")

            # 呼び出し式を抽出
            for node in ast.walk(tree):
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                    call = node.value
                    func_name = call.func.id

                    # パラメータ抽出
                    kwargs = {}
                    for kw in call.keywords:
                        kwargs[kw.arg] = ast.literal_eval(kw.value)

                    self.run_function(func_name, kwargs)

        except Exception as e:
            logging.error(f"アクション解析・実行エラー: {str(e)}")
            raise

    def run_function(self, func_name: str, params: dict):
        """
        登録された関数を実行
        """
        if func_name not in FUNCTIONS:
            logging.warning(f"未登録関数の呼び出し試行: {func_name}")
            return

        try:
            func = FUNCTIONS[func_name]
            func(**params)
            logging.info(f"関数 {func_name} 実行成功")

        except Exception as e:
            logging.error(f"関数 {func_name} 実行失敗: {str(e)}")
