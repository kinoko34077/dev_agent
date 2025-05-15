# api_client.py

import os
import yaml
import google.generativeai as genai
from dotenv import load_dotenv
import logging
from memory.memory_manager import MemoryManager

# ----------------------------------------
# GeminiClient: Google Gemini APIとのやり取りを担うクラス
# - .env や config.yaml から設定を読み込み
# - prompt_base.md を system prompt として使用
# - MemoryManager から履歴を取得し、チャットセッションを構築
# - `ask()` でプロンプト送信、`parse_response()` で分離処理
# ----------------------------------------

# 環境変数を読み込み（.envファイルの内容を環境変数として登録）
load_dotenv()


class GeminiClient:
    """
    Google Gemini API を利用したチャットクライアントクラス。
    設定ファイル（config.yaml）とシステムプロンプト（prompt_base.md）を用いて初期化され、
    メモリから過去の履歴を取得し、チャットセッションとして保持する。
    """

    def __init__(self):
        # .envからAPIキーを取得
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEYが設定されていません。.envファイルを確認してください。")

        # APIキーを用いてGeminiを初期化
        genai.configure(api_key=api_key)

        # config.yaml を読み込み（モデル名や温度設定、履歴数を取得）
        config_path = os.path.join(os.getcwd(), "config/config.yaml")
        if not os.path.exists(config_path):
            raise FileNotFoundError("config.yamlが存在しません。プロジェクト直下に配置してください。")

        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        # モデル設定
        self.model_name = config.get("model", {}).get("name", os.getenv("MODEL_NAME", "gemini-1.5-pro"))
        self.temperature = config.get("model", {}).get("temperature", 0.7)
        self.recent_turns = config.get("memory", {}).get("recent_turns", 3)

        # システムプロンプトとなるベースプロンプトファイルを取得
        prompt_base_path = config.get("model", {}).get("prompt_base_path", "prompt_base.md")
        if not os.path.exists(prompt_base_path):
            raise FileNotFoundError(f"{prompt_base_path}が存在しません。")

        with open(prompt_base_path, "r", encoding="utf-8") as f:
            self.system_instruction = f.read()

        # モデルインスタンス生成（チャット生成モード）
        self.model = genai.GenerativeModel(
            model_name=self.model_name,
            system_instruction=self.system_instruction,
            generation_config={
                "temperature": self.temperature
            }
        )

        # 履歴（直近ターン数分）を読み込んでセッション初期化
        memory = MemoryManager()
        history = memory.get_recent_history(self.recent_turns)
        self.chat = self.model.start_chat(history=history)

        logging.info(f"GeminiClient 初期化完了：使用モデル = {self.model_name}, 温度 = {self.temperature}")

    def ask(self, prompt: str) -> object:
        """
        プロンプトをGeminiチャットに送信し、応答オブジェクトを返す。

        Args:
            prompt (str): ユーザーからの入力文字列

        Returns:
            object: Geminiの応答オブジェクト
        """
        try:
            response = self.chat.send_message(prompt)
            return response
        except Exception as e:
            logging.error(f"Gemini API呼び出しエラー: {str(e)}")
            raise

    def parse_response(self, response: object) -> tuple:
        """
        Geminiからの応答テキストを解析し、
        通常の応答部分と【実行】ブロック（YAMLなど）を分離する。

        Args:
            response (object): Geminiの応答オブジェクト

        Returns:
            tuple:
                content (str): 通常の返答文
                actions (str or None): 【実行】ブロックの内容（存在しない場合はNone）
        """
        try:
            text = response.text

            # 応答に【実行】ブロックが含まれているか判定
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
