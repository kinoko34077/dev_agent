# api/client.py

import logging
from dotenv import load_dotenv
import google.generativeai as genai

from utils.config_loader import load_config, get_env_key, get_prompt_base
from memory.memory_manager import MemoryManager
# FUNCTION_SCHEMA のimportは不要になるため削除
# from api.functions_schema import FUNCTION_SCHEMA

class LLMClient:
    # get_function_schemas_func: Function Callingのためのスキーマを取得するCallableを引数に追加
    def __init__(self, role: str = "thinker", get_function_schemas_func: callable = None):
        load_dotenv()
        config = load_config()

        # 各種設定値取得
        client_conf = config.get("clients", {}).get(role, {}) # 不要なバックスラッシュを削除
        model_conf = config.get("model", {}) # 不要なバックスラッシュを削除

        api_key_name = client_conf.get("api_key", model_conf.get("api_key", "GEMINI_API_KEY"))
        api_key = get_env_key(api_key_name) # 不要なバックスラッシュを削除
        genai.configure(api_key=api_key) # 不要なバックスラッシュを削除

        self.model_name = client_conf.get("model_name", model_conf.get("name", "gemini-1.5-flash"))
        self.temperature = client_conf.get("temperature", model_conf.get("temperature", 0.7))
        self.recent_turns = client_conf.get("recent_turns", config.get("memory", {}).get("recent_turns", 3)) # 不要なバックスラッシュを削除
        self.system_instruction = get_prompt_base()
        self.role = role

        # Function用ツール設定
        tools = None
        # roleが"function"の場合、引数で渡された関数を使ってスキーマを取得
        if role == "function" and get_function_schemas_func:
            try:
                function_schemas = get_function_schemas_func()
                if function_schemas:
                    tools = [{"function_declarations": function_schemas}]
                    logging.info(f"Function Callingスキーマをロードしました ({len(function_schemas)}個)")
                else:
                    logging.warning("Function Callingスキーマが取得されませんでした。")
            except Exception as e:
                logging.error(f"Function Callingスキーマの取得中にエラーが発生しました: {e}")


        self.model = genai.GenerativeModel(
            model_name=self.model_name,
            system_instruction=self.system_instruction,
            tools=tools,
            generation_config={"temperature": self.temperature}
        )

        if role in {"thinker", "recur"}:
            history = MemoryManager().get_recent_history(self.recent_turns)
            self.chat = self.model.start_chat(history=history)
        else:
            self.chat = self.model.start_chat()

        logging.info(f"{role}クライアント初期化完了：{self.model_name}")

    def ask(self, prompt: str) -> str:
        try:
            response = self.chat.send_message(prompt)
            return response.text
        except Exception as e:
            logging.error(f"LLMClient.ask エラー: {str(e)}")
            return "❌ 応答生成に失敗しました"

    def invoke(self, prompt: str) -> dict:
        try:
            response = self.chat.send_message(prompt)
            for part in response.candidates[0].content.parts: # 不要なバックスラッシュを削除
                if hasattr(part, "function_call"): # 不要なバックスラッシュを削除
                    return part.function_call
            return None
        except Exception as e:
            logging.error(f"LLMClient.invoke エラー: {str(e)}")
            return None

    def respond_with_result(self, function_name: str, result: dict) -> str:
        try:
            response_part = genai.glm.Part.from_function_response(name=function_name, response=result)
            response = self.chat.send_message(genai.glm.Content(role="user", parts=[response_part]))
            return response.text
        except Exception as e:
            logging.error(f"LLMClient.respond_with_result エラー: {str(e)}")
            return "❌ function_response送信失敗"