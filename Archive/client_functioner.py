# api/client_functioner.py

import os
import yaml
import logging
from dotenv import load_dotenv
import google.generativeai as genai

from api.functions_schema import FUNCTION_SCHEMA

class GeminiFunctionClient:
    """
    Gemini Function Calling対応クライアント
    - 関数スキーマ（FUNCTION_SCHEMA）に基づいてLLMが function_call を返す
    - GPTのFunction Callingと同等の構造で動作
    """

    def __init__(self):
        load_dotenv()  # .envから読み込み

        # 別APIキーを使用（GEMINI_API_KEY2）
        api_key = os.getenv("GEMINI_API_KEY2")
        if not api_key:
            raise ValueError("GEMINI_API_KEY2が未設定です。")

        genai.configure(api_key=api_key)

        # config.yaml 読み込み
        config_path = os.path.join(os.getcwd(), "config/config.yaml")
        if not os.path.exists(config_path):
            raise FileNotFoundError("config.yaml が存在しません。")

        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        self.model_name = config.get("model", {}).get("FunctionClient", "gemini-1.5-pro")
        self.temperature = 0.0  # 関数呼び出しは論理重視で固定低温度

        # モデル初期化（toolsに関数スキーマを設定）
        self.model = genai.GenerativeModel(
            model_name=self.model_name,
            tools=[{"function_declarations": FUNCTION_SCHEMA}],
            generation_config={"temperature": self.temperature}
        )

        self.chat = self.model.start_chat()
        logging.info(f"GeminiFunctionClient 初期化完了：{self.model_name}")

    def invoke(self, user_prompt: str) -> dict:
        """
        ユーザーの自然言語入力を受け取り、Geminiが返す function_call を取得する。

        Args:
            user_prompt (str): 実行指示的な自然文（例：「ログを追加して」）

        Returns:
            dict: function_call 構造（name, args）または None
        """
        try:
            response = self.chat.send_message(user_prompt)
            parts = response.candidates[0].content.parts

            for part in parts:
                if hasattr(part, "function_call"):
                    return part.function_call  # dict形式で返却される
            return None  # function_callが含まれていなかった場合

        except Exception as e:
            logging.error(f"Function Calling失敗: {str(e)}")
            return None

    def respond_with_result(self, function_name: str, result: dict) -> str:
        """
        実行結果（function_response）をGeminiに返して、次の応答を得る。

        Args:
            function_name (str): 関数名（例: "add_log"）
            result (dict): 実行結果（自由形式）

        Returns:
            str: Geminiからの最終的な出力応答
        """
        try:
            # function_response構文（ドキュメント形式）
            response = {
                "role": "user",
                "parts": [{
                    "function_response": {
                        "name": function_name,
                        "response": result
                    }
                }]
            }

            final_response = self.chat.send_message(response)
            return final_response.text

        except Exception as e:
            logging.error(f"function_response送信エラー: {str(e)}")
            return "❌ function_response送信失敗"
