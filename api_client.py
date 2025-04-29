import os
import yaml
import google.generativeai as genai
from dotenv import load_dotenv
import logging
from memory_manager import MemoryManager

# 環境変数読み込み (.env対応)
load_dotenv()

class GeminiClient:
    def __init__(self):
        # APIキー取得
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEYが設定されていません。.envファイルを確認してください。")

        genai.configure(api_key=api_key)

        # config.yaml読み込み
        config_path = os.path.join(os.getcwd(), "config.yaml")
        if not os.path.exists(config_path):
            raise FileNotFoundError("config.yamlが存在しません。プロジェクト直下に配置してください。")

        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        self.model_name = config.get("model", {}).get("name", os.getenv("MODEL_NAME", "gemini-1.5-pro"))
        self.temperature = config.get("model", {}).get("temperature", 0.7)
        self.recent_turns = config.get("memory", {}).get("recent_turns", 3)

        prompt_base_path = config.get("model", {}).get("prompt_base_path", "prompt_base.md")
        if not os.path.exists(prompt_base_path):
            raise FileNotFoundError(f"{prompt_base_path}が存在しません。")

        # prompt_base.mdからシステムプロンプト読み込み
        with open(prompt_base_path, "r", encoding="utf-8") as f:
            self.system_instruction = f.read()

        # モデル初期化（チャットモード）
        self.model = genai.GenerativeModel(
            model_name=self.model_name,
            system_instruction=self.system_instruction,
            generation_config={
                "temperature": self.temperature
            }
        )

        # 過去の履歴を読み込み
        memory = MemoryManager()
        history = memory.get_recent_history(self.recent_turns)

        self.chat = self.model.start_chat(history=history)

        logging.info(f"GeminiClient 初期化完了：使用モデル = {self.model_name}, 温度 = {self.temperature}")

    def ask(self, prompt: str) -> object:
        """ ユーザー指示をチャット形式で送信して応答を得る """
        try:
            response = self.chat.send_message(prompt)
            return response
        except Exception as e:
            logging.error(f"Gemini API呼び出しエラー: {str(e)}")
            raise

    def parse_response(self, response: object) -> tuple:
        """ Geminiの応答から本文と【実行】ブロックを分離 """
        try:
            text = response.text

            if "【実行】" in text:
                parts = text.split("【実行】")
                content = parts[0].strip()
                actions = parts[1].strip()
            else:
                content = text.strip()
                actions = None

            return content, actions

        except Exception as e:
            logging.error(f"応答パースエラー: {str(e)}")
            raise
