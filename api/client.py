# api/client.py

import os
import yaml
import logging
from dotenv import load_dotenv
import google.generativeai as genai

from memory.memory_manager import MemoryManager
from api.functions_schema import FUNCTION_SCHEMA  # 必要に応じて


class LLMClient:
    def __init__(self, role: str = "thinker"):
        load_dotenv()

        config_path = os.path.join(os.getcwd(), "config", "config.yaml")
        if not os.path.exists(config_path):
            raise FileNotFoundError("config.yamlが存在しません")

        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        # クライアントごとの設定（無ければmodel設定とデフォルト）
        client_conf = config.get("clients", {}).get(role, {})
        default_model = config.get("model", {}).get("name", "gemini-1.5-flash")

        api_key_name = client_conf.get("api_key", config.get("model", {}).get("api_key", "GEMINI_API_KEY"))
        api_key = os.getenv(api_key_name)
        if not api_key:
            raise ValueError(f"{role}用APIキー({api_key_name})が未設定です")

        genai.configure(api_key=api_key)

        self.model_name = client_conf.get("model_name", default_model)
        self.temperature = client_conf.get("temperature", config.get("model", {}).get("temperature", 0.7))
        self.recent_turns = client_conf.get("recent_turns", config.get("memory", {}).get("recent_turns", 3))
        self.role = role

        # プロンプト読み込み
        prompt_path = config.get("model", {}).get("prompt_base_path", "prompts/prompt_base.md")
        self.system_instruction = "あなたは有能なアシスタントです。"
        if os.path.exists(prompt_path):
            with open(prompt_path, "r", encoding="utf-8") as f:
                self.system_instruction = f.read()

        # Function用ツール指定
        tools = None
        if role == "function":
            tools = [{"function_declarations": FUNCTION_SCHEMA}]

        self.model = genai.GenerativeModel(
            model_name=self.model_name,
            system_instruction=self.system_instruction,
            tools=tools,
            generation_config={"temperature": self.temperature}
        )

        # チャット履歴
        if role in {"thinker", "recur"}:
            history = MemoryManager().get_recent_history(self.recent_turns)
            self.chat = self.model.start_chat(history=history)
        else:
            self.chat = self.model.start_chat()

        logging.info(f"{role}クライアント初期化完了：{self.model_name}")

    def ask(self, prompt: str) -> str:
        """通常の自然文応答を返す"""
        try:
            response = self.chat.send_message(prompt)
            return response.text
        except Exception as e:
            logging.error(f"LLMClient.ask エラー: {str(e)}")
            return "❌ 応答生成に失敗しました"

    def invoke(self, prompt: str) -> dict:
        """Function Calling: 関数呼び出し構造を抽出"""
        try:
            response = self.chat.send_message(prompt)
            for part in response.candidates[0].content.parts:
                if hasattr(part, "function_call"):
                    return part.function_call
            return None
        except Exception as e:
            logging.error(f"LLMClient.invoke エラー: {str(e)}")
            return None

    def respond_with_result(self, function_name: str, result: dict) -> str:
        from google.generativeai.types import Content, Part
        try:
            response_part = Part.from_function_response(name=function_name, response=result)
            response = self.chat.send_message(Content(role="user", parts=[response_part]))
            return response.text
        except Exception as e:
            logging.error(f"LLMClient.respond_with_result エラー: {str(e)}")
            return "❌ function_response送信失敗"
