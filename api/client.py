# ... existing code ...
import logging
from dotenv import load_dotenv
import google.generativeai as genai
from google.protobuf.json_format import MessageToDict
import json

# 必要なprotobufクラスをインポート
from google.ai.generativelanguage_v1beta.types.content import Part # この行を追加
# from google.ai.generativelanguage_v1beta.types.content import Content # 必要であればこちらもインポートするが、send_messageはContentオブジェクトを受け付けるため、genai.types.Contentでも動作する可能性が高い

from utils.helpers import setup_logger
from utils.config_loader import load_config, get_env_key, get_prompt_base
from memory.memory_manager import MemoryManager
# FUNCTION_SCHEMA のimportは不要になるため削除
# from api.functions_schema import FUNCTION_SCHEMA


# ロガーの設定を強制的に更新
setup_logger(mode="timestamp", force=True)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class LLMClient:
    # get_function_schemas_func: Function Callingのためのスキーマを取得するCallableを引数に追加
    def __init__(self, role: str = "thinker", get_function_schemas_func: callable = None):
        load_dotenv()
        config = load_config()

        # 各種設定値取得
        client_conf = config.get("clients", {}).get(role, {})
        model_conf = config.get("model", {})

        api_key_name = client_conf.get("api_key", model_conf.get("api_key", "GEMINI_API_KEY"))
        api_key = get_env_key(api_key_name)
        genai.configure(api_key=api_key)

        # デフォルトモデルをgemini-2.0-flashに更新
        self.model_name = client_conf.get("model_name", model_conf.get("name", "gemini-2.0-flash"))
        self.temperature = client_conf.get("temperature", model_conf.get("temperature", 0.7))
        self.recent_turns = client_conf.get("recent_turns", config.get("memory", {}).get("recent_turns", 3))
        self.system_instruction = get_prompt_base()
        self.role = role
        self.get_function_schemas_func = get_function_schemas_func

        # 生成設定を初期化
        self.generation_config = {
            "temperature": self.temperature,
            "top_p": 0.8,
            "top_k": 40,
            "max_output_tokens": 2048,
        }

        # Function用ツール設定
        tools = None
        if role == "function" and get_function_schemas_func:
            try:
                function_schemas = get_function_schemas_func()
                if function_schemas:
                    # ツールの形式を公式ドキュメントに合わせて更新
                    tools = {
                        "function_declarations": function_schemas
                    }
                    logger.info(f"Function Callingスキーマをロードしました ({len(function_schemas)}個)")
                else:
                    logger.warning("Function Callingスキーマが取得されませんでした。")
            except Exception as e:
                logger.error(f"Function Callingスキーマの取得中にエラーが発生しました: {e}")

        self.model = genai.GenerativeModel(
            model_name=self.model_name,
            system_instruction=self.system_instruction,
            tools=tools,
            generation_config=self.generation_config
        )

        if role in {"thinker", "recur"}:
            history = MemoryManager().get_recent_history(self.recent_turns)
            self.chat = self.model.start_chat(history=history)
        else:
            self.chat = self.model.start_chat()

        logger.info(f"{role}クライアント初期化完了：{self.model_name}")

    def ask(self, prompt: str) -> str:
        try:
            response = self.chat.send_message(prompt)
            return response.text
        except Exception as e:
            logger.error(f"LLMClient.ask エラー: {str(e)}")
            return "❌ 応答生成に失敗しました"

    def invoke(self, prompt: str) -> dict:
        try:
            response = self.chat.send_message(prompt)
            for part in response.candidates[0].content.parts: # 不要なバックスラッシュを削除
                if hasattr(part, "function_call"): # 不要なバックスラッシュを削除
                    return part.function_call
            return None
        except Exception as e:
            logger.error(f"LLMClient.invoke エラー: {str(e)}")
            return None
# ... existing code ...
    def respond_with_result(self, function_name: str, result: dict) -> tuple[str, list]: # 戻り値の型ヒントを変更
        """
        Function Calling の結果をLLMに送信し、その後の応答を取得します。

        Args:
            function_name: 実行した関数名
            result: 関数実行結果の辞書

        Returns:
            tuple[str, list]: (応答テキスト, 関数呼び出しのリスト) - 関数結果フィードバック後のLLMからの応答
        """
        try:
            logger.info(f"関数結果をLLMに送信: {function_name}, {result}")

            # 新しいライブラリの記法に合わせて修正
            # genai.types.Part -> 直接インポートした Part クラスを使用
            # genai.types.Content はそのまま使用してみる（必要であれば修正）

            # Function Response を含む Content オブジェクトを作成
            # 直接インポートした Part クラスを使用
            response_part = Part.from_function_response( # 修正箇所: genai.types.Part -> Part
                name=function_name,
                response=result
            )

            # send_message メソッドの第一引数は Content オブジェクトまたは文字列
            # 関数応答をContentとして送信
            response = self.chat.send_message(
                genai.types.Content(role="user", parts=[response_part])
            )

            logger.info("LLM応答取得完了 (関数結果フィードバック後)")
            logger.info(f"応答型: {type(response)}")
            # logger.info(f"応答内容: {response}") # 内容は長いのでコメントアウト
            logger.info(f"応答の属性: {dir(response)}")


            # ask_with_functions と同様の応答解析ロジックを追加
            function_calls = []
            extracted_texts = []

            if hasattr(response, 'candidates') and response.candidates:
                candidate = response.candidates[0]
                if hasattr(candidate, 'content') and hasattr(candidate.content, 'parts'):
                    parts = candidate.content.parts

                    for i, part in enumerate(parts):
                        logger.info(f"=== part詳細 (respond_with_result, part {i}) ===")
                        logger.info(f"part: {part}")
                        logger.info(f"part型: {type(part)}")
                        try:
                            logger.info(f"partの属性: {dir(part)}")
                        except TypeError:
                            logger.info("partはdir()をサポートしていません。")

                        # Function Callが含まれているかを確認
                        if hasattr(part, 'function_call'):
                            func_call_obj = part.function_call
                            logger.info(f"=== function_call属性検出 (respond_with_result) ===")
                            logger.info(f"func_call_obj: {func_call_obj}")
                            logger.info(f"func_call_obj型: {type(func_call_obj)}")
                            try:
                                logger.info(f"func_call_objの属性: {dir(func_call_obj)}")
                            except TypeError:
                                logger.info("func_call_objはdir()をサポートしていません。")

                            try:
                                # FunctionCallオブジェクトから名前と引数を抽出
                                if hasattr(func_call_obj, 'name') and hasattr(func_call_obj, 'args'):
                                     name = func_call_obj.name
                                     args = func_call_obj.args # argsは通常dict
                                     logger.info("function_call_obj は属性を持つオブジェクトです。")
                                else:
                                     # 万が一、属性がない場合のフォールバック（ログで型の確認が必要）
                                     logger.warning(f"未知のfunction_callオブジェクト構造: {type(func_call_obj)}")
                                     name = None
                                     args = {}

                                if name:
                                    # args が MapComposite オブジェクトとして渡される場合があるので dict に変換
                                    if hasattr(args, 'to_dict'):
                                        args_dict = args.to_dict()
                                    elif isinstance(args, dict):
                                        args_dict = args
                                    else:
                                         logger.warning(f"未知のargs型: {type(args)}")
                                         args_dict = {} # 変換できない場合は空の辞書とする

                                    function_calls.append({
                                        "name": name,
                                        "arguments": args_dict
                                    })
                                    logger.info(f"関数呼び出しを検出・追加 (respond_with_result): {name} (args: {args_dict})")
                                else:
                                     logger.warning("Function call オブジェクトから名前を抽出できませんでした (respond_with_result)。")


                            except Exception as e:
                                logger.warning(f"関数呼び出しの解析中にエラーが発生 (respond_with_result): {str(e)}")
                                logger.warning(f"エラーの詳細: {type(e).__name__}: {str(e)}")
                                import traceback
                                logger.warning(f"トレースバック:\n{traceback.format_exc()}")
                                continue # このFunction Callの処理をスキップして次に進む

                        # テキストが含まれているかを確認 (text属性の存在で判断)
                        elif hasattr(part, 'text'):
                            logger.info("=== text属性検出 (respond_with_result) ===")
                            extracted_texts.append(part.text)
                            logger.info(f"テキストパートを検出・追加 (respond_with_result): {part.text}")
                        else:
                             logger.info("partはfunction_call属性もtext属性も持っていません (respond_with_result)。")


            # 抽出したテキストパートを結合
            response_text = "".join(extracted_texts)
            logger.info(f"最終的な抽出テキスト応答 (respond_with_result): {response_text}")

            logger.info("=== respond_with_result 終了 ===")
            # 抽出したテキストと関数呼び出しリストを返す
            return response_text, function_calls

        except Exception as e:
            logger.error(f"LLMClient.respond_with_result エラーが発生しました。")
            logger.error(f"エラーの詳細: {type(e).__name__}: {str(e)}")
            import traceback
            logger.error(f"トレースバック:\n{traceback.format_exc()}")

            # エラー発生箇所の行番号を特定
            try:
                 tb = e.__traceback__
                 while tb is not None and tb.tb_frame.f_code.co_filename != __file__:
                     tb = tb.tb_next
                 if tb:
                     logger.error(f"エラーの発生が疑われる行 (client.py内): {tb.tb_lineno}")
                 else:
                      logger.error("client.py 内でエラーの行番号を特定できませんでした。")
            except AttributeError:
                 logger.error("エラーの行番号を特定できませんでした (tb_lineno属性なし)。")

            # エラーを再raise
            raise # エラーを呼び出し元に伝える

    def ask_with_functions(self, prompt: str) -> tuple[str, list]:
        """
        Function Calling APIを使用してLLMに処理を依頼します。

        Args:
            prompt: ユーザーからの入力プロンプト

        Returns:
            tuple[str, list]: (応答テキスト, 関数呼び出しのリスト)
        """
        try:
            logger.info("=== ask_with_functions 開始 ===")
            logger.info(f"プロンプト: {prompt}")

            # ここでsend_messageが呼ばれ、LLMからの応答を受け取る
            response = self.chat.send_message(
                prompt,
                generation_config=self.generation_config
            )

            logger.info("=== レスポンス詳細 ===")
            logger.info(f"レスポンス型: {type(response)}")
            # responseオブジェクト全体をログ出力 (デバッグ目的で一時的に詳細に出力)
            # 大量の出力になる可能性があるので注意
            # logger.info(f"レスポンス内容: {response}") # 内容は長いのでコメントアウト
            # responseオブジェクトの持つ属性を確認
            logger.info(f"レスポンスの属性: {dir(response)}")

            # Function Callとテキスト応答を格納するリストと文字列を初期化
            function_calls = []
            extracted_texts = [] # テキストパートを保持するリスト

            # LLM応答の構造を段階的に確認し、partsからFunction Callとテキストを抽出
            if hasattr(response, 'candidates') and response.candidates:
                logger.info("candidates属性を確認")
                # logger.info(f"candidates内容: {response.candidates}") # 内容は長いのでコメントアウト

                candidate = response.candidates[0]
                logger.info(f"=== candidate詳細 ===")
                # logger.info(f"candidate: {candidate}") # 内容は長いのでコメントアウト
                logger.info(f"candidate型: {type(candidate)}")
                logger.info(f"candidateの属性: {dir(candidate)}")


                if hasattr(candidate, 'content') and hasattr(candidate.content, 'parts'):
                    content = candidate.content
                    logger.info(f"=== content詳細 ===")
                    # logger.info(f"content: {content}") # 内容は長いのでコメントアウト
                    logger.info(f"content型: {type(content)}")
                    logger.info(f"contentの属性: {dir(content)}")

                    parts = content.parts
                    logger.info("parts属性を確認")
                    logger.info(f"parts型: {type(parts)}")
                    # logger.info(f"parts内容: {parts}") # 内容は長いのでコメントアウト

                    # parts リストをイテレートして Function Callとテキストを抽出
                    for i, part in enumerate(parts):
                        logger.info(f"=== part詳細 (part {i}) ===")
                        logger.info(f"part: {part}")
                        logger.info(f"part型: {type(part)}")
                        try:
                            logger.info(f"partの属性: {dir(part)}")
                        except TypeError:
                            logger.info("partはdir()をサポートしていません。")

                        # Function Callが含まれているかを確認
                        # 以前のデバッグでfunction_callがdictで返るとの情報があったため、dictとして処理
                        # ただし、Function Call の part は genai.types.FunctionCall のようなオブジェクトである可能性も考慮し、hasattrでfunction_call属性の存在を確認するのが安全
                        if hasattr(part, 'function_call'):
                            # Function Call の part オブジェクトから function_call 情報を取得
                            # ここで取得される part.function_call が dict なのか protobuf オブジェクトなのかを再度ログで確認するとより確実
                            func_call_obj = part.function_call
                            logger.info(f"=== function_call属性検出 ===")
                            logger.info(f"func_call_obj: {func_call_obj}")
                            logger.info(f"func_call_obj型: {type(func_call_obj)}")
                            try:
                                logger.info(f"func_call_objの属性: {dir(func_call_obj)}")
                            except TypeError:
                                logger.info("func_call_objはdir()をサポートしていません。")


                            try:
                                # func_call_obj が dict の場合
                                if isinstance(func_call_obj, dict):
                                     name = func_call_obj.get("name")
                                     args = func_call_obj.get("args", {}) # args は存在しない可能性があるのでgetを使う
                                     logger.info("function_call_obj は dict です。")
                                # func_call_obj が protobuf オブジェクトの場合 (genai.types.FunctionCall など)
                                # whichOneof はこの型のオブジェクトに対して呼ばれる可能性がある
                                elif hasattr(func_call_obj, 'name') and hasattr(func_call_obj, 'args'):
                                     name = func_call_obj.name
                                     args = func_call_obj.args # argsは通常dict
                                     logger.info("function_call_obj は属性を持つオブジェクトです。")
                                else:
                                     logger.warning(f"未知のfunction_callオブジェクト型: {type(func_call_obj)}")
                                     name = None
                                     args = {}


                                if name:
                                    function_calls.append({
                                        "name": name,
                                        "arguments": args
                                    })
                                    logger.info(f"関数呼び出しを検出・追加: {name} (args: {args})")
                                else:
                                     logger.warning("Function call オブジェクトから名前を抽出できませんでした。")


                            except Exception as e:
                                logger.warning(f"関数呼び出しの解析中にエラーが発生: {str(e)}")
                                logger.warning(f"エラーの詳細: {type(e).__name__}: {str(e)}")
                                import traceback
                                logger.warning(f"トレースバック:\n{traceback.format_exc()}")
                                continue # このFunction Callの処理をスキップして次に進む

                        # テキストが含まれているかを確認 (text属性の存在で判断)
                        elif hasattr(part, 'text'):
                            logger.info("=== text属性検出 ===")
                            extracted_texts.append(part.text)
                            logger.info(f"テキストパートを検出・追加: {part.text}")
                        else:
                             logger.info("partはfunction_call属性もtext属性も持っていません。")


            # 抽出したテキストパートを結合
            response_text = "".join(extracted_texts)
            logger.info(f"最終的な抽出テキスト応答: {response_text}")

            logger.info("=== ask_with_functions 終了 ===")
            return response_text, function_calls

        except Exception as e:
            logger.error(f"LLMClient.ask_with_functions エラーが発生しました。")
            logger.error(f"エラーの詳細: {type(e).__name__}: {str(e)}")
            # traceback情報を含める
            import traceback
            logger.error(f"トレースバック:\n{traceback.format_exc()}")

            # エラー発生箇所の行番号を特定 (tb_linenoは存在しない場合があるため要確認)
            try:
                 # トレースバックから、自分のコード（client.py）内のエラー発生箇所を特定
                 # send_message呼び出し自体でエラーが出た場合など、tb_linenoが正確でない可能性もある
                 tb = e.__traceback__
                 while tb is not None and tb.tb_frame.f_code.co_filename != __file__:
                     tb = tb.tb_next
                 if tb:
                     logger.error(f"エラーの発生が疑われる行 (client.py内): {tb.tb_lineno}")
                 else:
                      logger.error("client.py 内でエラーの行番号を特定できませんでした。")
            except AttributeError:
                 logger.error("エラーの行番号を特定できませんでした (tb_lineno属性なし)。")


            # エラーを再raiseして、呼び出し元で捕捉できるようにする
            raise