# core/executor.py

import ast
import logging
import inspect # inspectモジュールをインポート

# 関数レジストリとメモリ管理はモジュール構造に合わせて相対パスでimport
from core.functions_registry import REGISTERED_FUNCTIONS
from memory.memory_manager import MemoryManager
# function_loaderをインポート
from function.function_loader import load_functions_from_directory, SANDBOX_FUNCTIONS_DIR

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
        # Sandbox関数をロードし、コア関数と統合
        self.sandbox_functions = load_functions_from_directory(SANDBOX_FUNCTIONS_DIR)
        # コア関数とsandbox関数を統合（callableのみを抽出）
        self.available_functions = {
            name: func_info.callable for name, func_info in REGISTERED_FUNCTIONS.items()
        }
        self.available_functions.update(self.sandbox_functions)  # sandbox関数がコア関数より優先される
        logging.info(f"コア関数 {len(REGISTERED_FUNCTIONS)}個 と Sandbox関数 {len(self.sandbox_functions)}個 を統合しました。合計 {len(self.available_functions)}個の関数が利用可能です。")

    def get_function_schemas(self) -> list[dict]:
        """
        利用可能な全関数（コア+サンドボックス）のFunction Callingスキーマを生成する。

        Returns:
            list[dict]: Function Callingスキーマのリスト。
        """
        schemas = []
        # コア関数のスキーマを取得
        for name, func_info in REGISTERED_FUNCTIONS.items():
            try:
                schemas.append({
                    "name": name,
                    "description": func_info.metadata.description,
                    "parameters": func_info.schema
                })
            except Exception as e:
                logging.error(f"関数 {name} のスキーマ生成に失敗しました: {e}")
                continue

        # Sandbox関数のスキーマを生成
        for name, func in self.sandbox_functions.items():
            try:
                # Docstringから関数説明を取得 (最初の行を要約として使用)
                description = inspect.getdoc(func)
                if description:
                    description = description.strip().split('\n')[0] # 最初の行を取得

                # 関数のシグネチャからパラメータ情報を抽出
                signature = inspect.signature(func)
                parameters = {
                    "type": "object",
                    "properties": {},
                    "required": []
                }

                for param_name, param in signature.parameters.items():
                    if param_name == 'self': # クラスメソッドのselfをスキップ
                        continue

                    param_info = {}
                    # 型ヒントがあればtype情報に追加
                    if param.annotation != inspect.Parameter.empty:
                        param_info['type'] = param.annotation.__name__ if hasattr(param.annotation, '__name__') else str(param.annotation)
                    else:
                        param_info['type'] = 'string' # デフォルト値を設定

                    parameters["properties"][param_name] = param_info

                    # デフォルト値がないパラメータは必須とする
                    if param.default == inspect.Parameter.empty:
                        parameters["required"].append(param_name)

                # 必須パラメータがない場合、requiredリストは空にする
                if not parameters["required"]:
                    parameters.pop("required")

                schemas.append({
                    "name": name,
                    "description": description if description else f"{name} 関数の説明（docstringがありません）",
                    "parameters": parameters
                })
            except Exception as e:
                logging.error(f"関数 {name} のスキーマ生成に失敗しました: {e}")
                continue

        return schemas


    def execute(self, actions_code: str):
        """
        【実行】ブロックからPython呼び出し式を抽出し、実行する。

        Args:
            actions_code (str): YAMLブロックやGPT出力のPython形式の文字列
                                例: run_script(path="tools/sample.py")
        """
        # ... existing code ...
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
                    # 呼び出し対象関数名
                    func_name_node = call.func
                    if isinstance(func_name_node, ast.Name):
                        func_name = func_name_node.id
                    else:
                        # ast.Attributeなどの複雑な呼び出しには対応しない
                        logging.warning(f"サポートされていない関数呼び出し形式: {ast.dump(func_name_node)}")
                        continue


                    kwargs = {}
                    # 引数（キーワード形式）を辞書に変換
                    for kw in call.keywords:
                        try:
                            kwargs[kw.arg] = ast.literal_eval(kw.value)
                        except Exception as e:
                            logging.warning(f"引数 {kw.arg} の評価に失敗: {ast.dump(kw.value)} - {e}")
                            # 評価できない引数はスキップまたはエラーハンドリングを検討
                            continue


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
        # 統合されたavailable_functionsから関数を探す
        if func_name not in self.available_functions: # この行のインデントを修正
            warning_message = f"未登録関数の呼び出し試行: {func_name}"
            logging.warning(warning_message)
            self.handle_error(warning_message)
            return

        try:
            func = self.available_functions[func_name]
            func(**params)
            logging.info(f"関数 {func_name} 実行成功")

        except Exception as e:
            error_message = f"関数 {func_name} 実行失敗: {str(e)} - パラメータ: {params}" # パラメータもログに出力
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
                    f"【前回エラー報告】\\n{error_message}\\n\\n"
                    "【指示】\\n"
                    "以下から適切な行動を選び、次に進んでください：\\n"
                    "1. エラーを修正して再試行する\\n"
                    "2. 別の方法を考える\\n"
                    "3. 今回のタスクを放棄する"
                ),
                actions=None
            )
            logging.info("エラープロンプト挿入成功")

        except Exception as e:
            logging.error(f"エラープロンプト挿入失敗: {str(e)}")
