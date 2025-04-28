# executor.py

import ast
import logging
from functions_registry import FUNCTIONS
from memory_manager import MemoryManager

class Executor:
    def __init__(self):
        logging.info("Executor初期化完了")
        self.memory = MemoryManager()

    def execute(self, actions_code: str):
        """
        【実行】ブロックからPython呼び出し式を解析・実行
        """
        if not actions_code:
            logging.info("実行アクションなし")
            return

        try:
            code = actions_code.strip()
            tree = ast.parse(code, mode="exec")

            for node in ast.walk(tree):
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                    call = node.value
                    func_name = call.func.id

                    kwargs = {}
                    for kw in call.keywords:
                        kwargs[kw.arg] = ast.literal_eval(kw.value)

                    self.run_function(func_name, kwargs)

        except Exception as e:
            error_message = f"アクション解析・実行エラー: {str(e)}"
            logging.error(error_message)
            self.handle_error(error_message)

    def run_function(self, func_name: str, params: dict):
        """
        登録された関数を実行
        """
        if func_name not in FUNCTIONS:
            warning_message = f"未登録関数の呼び出し試行: {func_name}"
            logging.warning(warning_message)
            self.handle_error(warning_message)
            return

        try:
            func = FUNCTIONS[func_name]
            func(**params)
            logging.info(f"関数 {func_name} 実行成功")

        except Exception as e:
            error_message = f"関数 {func_name} 実行失敗: {str(e)}"
            logging.error(error_message)
            self.handle_error(error_message)

    def handle_error(self, error_message: str):
        """
        エラー発生時の記録と、リカバリープロンプト自動挿入
        """
        try:
            self.memory.update(
                user_input="(システム) 前回エラー発生により、リカバリー指示を求めます。",
                model_output=(
                    f"【前回エラー報告】\n{error_message}\n\n"
                    "【指示】\n"
                    "以下から適切な行動を選び、次に進んでください：\n"
                    "1. エラーを修正して再試行する\n"
                    "2. 別の方法を考える\n"
                    "3. 今回のタスクを放棄する"
                ),
                actions=None
            )
            logging.info("エラープロンプト挿入成功")

        except Exception as e:
            logging.error(f"エラープロンプト挿入失敗: {str(e)}")
