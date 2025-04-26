# api_client.py

import os
import yaml
import google.generativeai as genai
from dotenv import load_dotenv
import logging

# 環境変数読み込み (.env対応)
load_dotenv()

class GeminiClient:
    def __init__(self):
        # .envからAPIキー取得
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEYが設定されていません。.envファイルを確認してください。")

        genai.configure(api_key=api_key)

        # config.yamlからモデル情報を取得
        config_path = os.path.join(os.getcwd(), "config.yaml")
        if not os.path.exists(config_path):
            raise FileNotFoundError("config.yamlが存在しません。プロジェクト直下に配置してください。")

        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        self.model_name = config.get("model", {}).get("name", "gemini-1.5-pro")
        self.temperature = config.get("model", {}).get("temperature", 0.7)

        self.model = genai.GenerativeModel(
            model_name=self.model_name,
            generation_config={
                "temperature": self.temperature
            }
        )

        logging.info(f"GeminiClient 初期化完了：使用モデル = {self.model_name}, 温度 = {self.temperature}")

    def ask(self, prompt: str) -> object:
        try:
            response = self.model.generate_content(
                contents=[
                    {"role": "user", "parts": [{"text": prompt}]}
                ]
            )
            return response
        except Exception as e:
            logging.error(f"Gemini API呼び出しエラー: {str(e)}")
            raise

    def parse_response(self, response: object) -> tuple:
        """
        Geminiの応答から本文と【実行】枠を分離して返す。
        - 本文: 通常の出力
        - 実行枠: YAML形式アクション（存在すれば）
        """
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
