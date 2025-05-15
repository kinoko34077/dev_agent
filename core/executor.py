# core/executor.py

import ast
import logging

# 関数レジストリとメモリ管理はモジュール構造に合わせて相対パスでimport
from core.functions_registry import FUNCTIONS
from memory.memory_manager import MemoryManager

class Executor:
    """
    【Executorクラス】
    - GeminiやGPTが出力する【実行】ブロック（Pythonコード）を解析・実行する責務を持つ
    - 安全なAST解析により、関数呼び出しと引数抽出を行う
    - 登録されていない関数は無視し、エラー処理に記録
    """

    def __init__(self):
        logging.info("Executor初期化完了")
        self.memory = MemoryManager()

    def execute(self, actions_code: str):
        """
        【実行】ブロックからPython呼び出し式を抽出し、実行する。

        Args:
            actions_code (str): YAMLブロックやGPT出力のPython形式の文字列
                                例: run_script(path="tools/sample.py")
        """
        if not actions_code:
            logging.info("実行アクションなし")
            return

        try:
            # コード文字列をPythonの抽象構文木(AST)としてパース
            code = actions_code.strip()
            tree = ast.parse(code, mode="exec")

            # ASTを歩いて関数呼び出し（Callノード）を探す
            for node in ast.walk(tree):
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                    call = node.value
                    func_name = call.func.id  # 呼び出し対象関数名

                    kwargs = {}
                    # 引数（キーワード形式）を辞書に変換
                    for kw in call.keywords:
                        kwargs[kw.arg] = ast.literal_eval(kw.value)

                    # 実際に関数を実行
                    self.run_function(func_name, kwargs)

        except Exception as e:
            error_message = f"アクション解析・実行エラー: {str(e)}"
            logging.error(error_message)
            self.handle_error(error_message)

    def run_function(self, func_name: str, params: dict):
        """
        関数レジストリから対象関数を実行する

        Args:
            func_name (str): 登録済み関数名
            params (dict): 関数に渡すキーワード引数
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
        実行エラー発生時の記録と、対話プロンプトへの自動挿入

        Args:
            error_message (str): 発生したエラーの内容（ログ／出力用）
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
