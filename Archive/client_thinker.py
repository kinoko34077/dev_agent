# api/client_thinker.py

import os
import yaml
import logging
import google.generativeai as genai
from dotenv import load_dotenv

from memory.memory_manager import MemoryManager

class GeminiThinkerClient:
    """
    通常応答（Function Callingしない）Geminiチャットクライアント
    - 要約や通常の自然言語出力向け
    """

    def __init__(self):
        load_dotenv()

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEYが設定されていません。")

        genai.configure(api_key=api_key)

        base_dir = os.path.dirname(os.path.abspath(__file__))  # ← core/main2.py の場所
        config_path = os.path.join(base_dir, "..", "config", "config.yaml")
        config_path = os.path.normpath(config_path)  # Windows用に正規化

        if not os.path.exists(config_path):
            raise FileNotFoundError(f"config.yamlが存在しません：{config_path}")

        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        self.model_name = config.get("model", {}).get("name", "gemini-1.5-flash")
        self.temperature = config.get("model", {}).get("temperature", 0.7)
        self.recent_turns = config.get("memory", {}).get("recent_turns", 4)

        prompt_path = config.get("model", {}).get("prompt_base_path", "templates/prompt_base.md")
        with open(prompt_path, "r", encoding="utf-8") as f:
            self.system_instruction = f.read()

        self.model = genai.GenerativeModel(
            model_name=self.model_name,
            system_instruction=self.system_instruction,
            generation_config={"temperature": self.temperature}
        )

        memory = MemoryManager()
        history = memory.get_recent_history(self.recent_turns)
        self.chat = self.model.start_chat(history=history)

        logging.info(f"GeminiThinkerClient 初期化完了：{self.model_name}")

    def ask(self, prompt: str) -> str:
        try:
            response = self.chat.send_message(prompt)
            return response.text
        except Exception as e:
            logging.error(f"GeminiThinkerClientエラー: {str(e)}")
            return "思考中にエラーが発生しました。"
