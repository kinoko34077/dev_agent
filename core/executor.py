# core/executor.py

import ast
import logging
import inspect # inspectモジュールをインポート
from utils.output_manager import output_manager, OutputType

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
        
        # 各マネージャーの初期化
        self.memory = MemoryManager()
        self.output_manager = output_manager
        
        # Sandbox関数をロードし、コア関数と統合
        self.sandbox_functions = load_functions_from_directory(SANDBOX_FUNCTIONS_DIR)
        self.available_functions = {
            name: func_info.callable for name, func_info in REGISTERED_FUNCTIONS.items()
        }
        self.available_functions.update(self.sandbox_functions)
        
        # LLMクライアントの初期化（Function Calling APIを使用）
        from api.client import LLMClient
        self.client = LLMClient(
            role="function",
            get_function_schemas_func=self.get_function_schemas
        )
        
        # 再帰処理マネージャーは必要になった時点で初期化
        self._recursion_manager = None
        
        logging.info(f"コア関数 {len(REGISTERED_FUNCTIONS)}個 と Sandbox関数 {len(self.sandbox_functions)}個 を統合しました。合計 {len(self.available_functions)}個の関数が利用可能です。")

    @property
    def recursion_manager(self):
        """
        再帰処理マネージャーを遅延初期化するプロパティ
        """
        if self._recursion_manager is None:
            from core.recursion_manager import RecursionManager
            self._recursion_manager = RecursionManager(main_func=self.execute)
        return self._recursion_manager

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

    def _extract_execution_block(self, response: str) -> str:
        """
        応答から【実行】ブロックを抽出する
        
        Args:
            response: LLMからの応答テキスト
            
        Returns:
            str: 抽出された【実行】ブロックの内容
        """
        try:
            if "【実行】" not in response:
                return ""
            
            # 【実行】ブロックの内容を抽出
            execution_block = response.split("【実行】")[1].strip()
            return execution_block
            
        except Exception as e:
            logging.error(f"実行ブロック抽出エラー: {str(e)}")
            return ""

    def _handle_function_calls(self, response: str) -> str:
        """
        応答から関数呼び出しを抽出して実行する
        
        Args:
            response: LLMからの応答テキスト
            
        Returns:
            str: 実行されたアクションの記録
        """
        execution_block = self._extract_execution_block(response)
        if not execution_block:
            return ""
            
        try:
            # コード文字列をPythonの抽象構文木(AST)としてパース
            tree = ast.parse(execution_block, mode="exec")
            
            # 実行結果を記録
            actions = []
            
            # ASTを歩いて関数呼び出し（Callノード）を探す
            for node in ast.walk(tree):
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                    call = node.value
                    # 呼び出し対象関数名
                    func_name_node = call.func
                    if isinstance(func_name_node, ast.Name):
                        func_name = func_name_node.id
                        
                        kwargs = {}
                        # 引数（キーワード形式）を辞書に変換
                        for kw in call.keywords:
                            try:
                                # 日本語の句読点を除去してから評価
                                value_str = ast.dump(kw.value)
                                value_str = value_str.replace('。', '.').replace('、', ',')
                                kwargs[kw.arg] = ast.literal_eval(value_str)
                            except Exception as e:
                                self.output_manager.output(
                                    f"引数 {kw.arg} の評価に失敗: {ast.dump(kw.value)} - {e}",
                                    OutputType.ERROR
                                )
                                continue
                        
                        # 関数を実行
                        self.run_function(func_name, kwargs)
                        # アクションの記録（デバッグモード用）
                        actions.append(f"{func_name}({', '.join(f'{k}={v}' for k, v in kwargs.items())})")
            
            # デバッグモードの場合のみアクションを返す
            if self.output_manager.debug_mode and actions:
                return "\n".join(actions)
            return ""
            
        except Exception as e:
            error_message = f"アクション解析・実行エラー: {str(e)}"
            self.output_manager.output(error_message, OutputType.ERROR)
            return error_message

    def execute(self, user_input: str) -> str:
        """
        ユーザー入力を処理し、エージェントの応答を返します。
        
        Args:
            user_input: ユーザーからの入力
            
        Returns:
            str: エージェントの応答
        """
        try:
            # ユーザー入力をログに記録
            self.output_manager.output(user_input, OutputType.USER)
            
            # ... existing code ...
            # 内的対話の条件チェック
            if self._check_dialogue_conditions(user_input):
                try:
                    return self._handle_self_dialogue(user_input)
                except Exception as e:
                    error_msg = f"内的対話処理中にエラーが発生: {str(e)}"
                    logging.error(error_msg)
                    return f"【本文】\n内的対話の処理に失敗しました。\nエラー内容: {str(e)}"

            # Function Calling APIを使用してLLMに処理を依頼 および 関数呼び出しの処理全体をtry...exceptで囲む
            try:
                logging.info("LLMClient.ask_with_functions呼び出し開始")
                response, function_calls = self.client.ask_with_functions(user_input)
                logging.info("LLMClient.ask_with_functions呼び出し完了")

                # 関数呼び出しの処理
                if function_calls:
                    logging.info(f"検出された関数呼び出し: {len(function_calls)}個")
                    for func_call in function_calls:
                        func_name = func_call.get("name")
                        func_args = func_call.get("arguments", {})

                        if func_name:
                            if func_name in self.available_functions:
                                try:
                                    logging.info(f"関数 '{func_name}' 実行開始 with args: {func_args}")
                                    result = self.run_function(func_name, func_args)
                                    logging.info(f"関数 '{func_name}' 実行完了")
                                    # 関数実行結果をLLMにフィードバック
                                    logging.info(f"関数結果をLLMに送信: {func_name}, {result}")
                                    response = self.client.respond_with_result(func_name, result)
                                    logging.info("LLM応答取得完了 (関数結果フィードバック後)")
                                except Exception as e:
                                    error_msg = f"関数 '{func_name}' の実行または結果送信に失敗: {str(e)}"
                                    logging.error(error_msg)
                                    # 関数実行失敗時はその旨を応答に含める
                                    return f"【本文】\nエラー: 関数 '{func_name}' の実行に失敗しました。\n詳細: {str(e)}"
                            else:
                                warning_msg = f"未登録の関数が呼び出されました: {func_name}"
                                logging.warning(warning_msg)
                                # 未登録関数呼び出し時はその旨を応答に含める
                                return f"【本文】\n警告: 未登録の関数 '{func_name}' が呼び出されました。"
                        else:
                             logging.warning("関数名が指定されていないfunction_callを検出しました。")
                             # 関数名がない場合は警告として応答に含める
                             return "【本文】\n警告: 関数名が指定されていません。"

                # 関数呼び出しがない場合、または関数呼び出し処理後の最終応答を返す
                # responseオブジェクトがテキスト応答を持つか確認
                if hasattr(response, 'text') and response.text:
                    return response.text
                else:
                    # テキスト応答がない場合は、何らかの処理が行われたが最終応答が生成されなかったと判断
                    logging.info("LLM応答にテキストが含まれていません。")
                    return "【本文】\n処理は実行されましたが、テキスト応答が生成されませんでした。"

            except Exception as e:
                # ask_with_functions呼び出し自体の失敗や、その他の予期せぬエラーをキャッチ
                error_msg = f"LLMとの通信または処理中にエラーが発生: {str(e)}"
                logging.error(error_msg)
                # LLM処理失敗時はエラー応答を返す
                return f"【本文】\nエラー: LLMとの通信または処理中に問題が発生しました。\n詳細: {str(e)}"

        except Exception as e:
            # executeメソッド全体での予期せぬエラー
            error_msg = f"Executor.execute内で予期せぬエラーが発生: {str(e)}"
            logging.error(error_msg)
            self.output_manager.output(error_msg, OutputType.ERROR) # output_managerでも出力
            return f"【本文】\nシステムエラー: {str(e)}"

    def _check_dialogue_conditions(self, user_input: str) -> bool:
        """
        内的対話を開始すべきかどうかを判断する
        
        Args:
            user_input: ユーザーからの入力テキスト
            
        Returns:
            bool: 内的対話を開始すべき場合はTrue
        """
        # 自己判断のための条件をチェック
        conditions = [
            # 1. 複雑な問題や不確実性が高い場合
            lambda x: len(x.split()) > 50,  # 長い入力
            lambda x: "?" in x or "？" in x,  # 質問を含む
            lambda x: "確認" in x or "確認して" in x,  # 確認要求
            
            # 2. 自己改善が必要な場合
            lambda x: "改善" in x or "最適化" in x,  # 改善要求
            lambda x: "問題" in x or "課題" in x,  # 問題提起
            
            # 3. 複数の機能を組み合わせる必要がある場合
            lambda x: len([f for f in REGISTERED_FUNCTIONS if f in x]) > 1,  # 複数関数
            
            # 4. 過去の経験を活かす必要がある場合
            lambda x: "以前" in x or "過去" in x or "履歴" in x,  # 過去参照
        ]
        
        # 条件のいずれかに一致する場合、内的対話を開始
        return any(condition(user_input) for condition in conditions)

    def _handle_self_dialogue(self, response: str) -> str:
        """
        自己判断による内的対話を処理する
        
        Args:
            response: 前回の応答
            
        Returns:
            str: 処理結果
        """
        try:
            # 内的対話を継続
            result = self.run_function(
                "continue_internal_dialogue",
                {"response": response}
            )
            if result and result.get("status") == "success":
                return result.get("analysis", "")
            return "内的対話の継続に失敗しました。"
        except Exception as e:
            logging.error(f"内的対話処理エラー: {str(e)}")
            return f"内的対話処理中にエラーが発生しました: {str(e)}"

    def _should_continue_dialogue(self, response: str) -> bool:
        """
        内的対話を継続すべきかどうかを判断する
        
        Args:
            response: 前回の応答
            
        Returns:
            bool: 内的対話を継続すべき場合はTrue
        """
        # 継続判断のための条件をチェック
        conditions = [
            # 1. 不確実性が残っている場合
            lambda x: "?" in x or "？" in x,
            lambda x: "確認" in x or "確認して" in x,
            
            # 2. より詳細な分析が必要な場合
            lambda x: "詳細" in x or "具体的" in x,
            lambda x: "検討" in x or "検証" in x,
            
            # 3. 複数の選択肢がある場合
            lambda x: "または" in x or "もしくは" in x,
            lambda x: "選択" in x or "選ぶ" in x,
        ]
        
        # 条件のいずれかに一致する場合、内的対話を継続
        return any(condition(response) for condition in conditions)

    def run_function(self, func_name: str, params: dict):
        """
        関数レジストリから対象関数を実行する

        Args:
            func_name (str): 登録済み関数名
            params (dict): 関数に渡すキーワード引数
            
        Returns:
            dict: 関数の実行結果
        """
        # 統合されたavailable_functionsから関数を探す
        if func_name not in self.available_functions:
            warning_message = f"未登録関数の呼び出し試行: {func_name}"
            logging.warning(warning_message)
            self.handle_error(warning_message)
            return None

        try:
            func = self.available_functions[func_name]
            result = func(**params)
            logging.info(f"関数 {func_name} 実行成功")
            return result

        except Exception as e:
            error_message = f"関数 {func_name} 実行失敗: {str(e)} - パラメータ: {params}"
            logging.error(error_message)
            self.handle_error(error_message)
            return None

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
