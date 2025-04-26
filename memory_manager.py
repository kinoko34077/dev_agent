# memory_manager.py

import os
import yaml
import json
import logging
from datetime import datetime

class MemoryManager:
    def __init__(self):
        self.memory_dir = "memory"
        self.inputs_dir = os.path.join(self.memory_dir, "inputs")
        self.outputs_dir = os.path.join(self.memory_dir, "outputs")
        self.summaries_dir = os.path.join(self.memory_dir, "summaries")

        # 設定読み込み
        config_path = os.path.join(os.getcwd(), "config.yaml")
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        self.recent_turns = config.get("memory", {}).get("recent_turns", 3)
        self.max_tokens = config.get("memory", {}).get("max_tokens", 100000)

        self._ensure_dirs()

    def _ensure_dirs(self):
        for d in [self.inputs_dir, self.outputs_dir, self.summaries_dir]:
            os.makedirs(d, exist_ok=True)

    def get_all_history(self):
        """
        過去のinputs/outputsをまとめて読み込み、履歴リスト化する
        """
        history = []
        input_files = sorted(os.listdir(self.inputs_dir))
        output_files = sorted(os.listdir(self.outputs_dir))

        for input_file, output_file in zip(input_files, output_files):
            with open(os.path.join(self.inputs_dir, input_file), "r", encoding="utf-8") as f_in:
                user_text = f_in.read()
            with open(os.path.join(self.outputs_dir, output_file), "r", encoding="utf-8") as f_out:
                model_text = f_out.read()

            history.append({"role": "user", "parts": [{"text": user_text}]})
            history.append({"role": "model", "parts": [{"text": model_text}]})

        return history[-(self.recent_turns*2):]  # ユーザー・モデルセットで数える

    def build_prompt(self, user_input: str) -> str:
        """
        現在の履歴＋新規指示を組み合わせたプロンプトを構築
        """
        history = self.get_all_history()
        prompt_parts = []

        for h in history:
            role = h["role"]
            text = h["parts"][0]["text"]
            if role == "user":
                prompt_parts.append(f"ユーザー: {text}")
            else:
                prompt_parts.append(f"エージェント: {text}")

        prompt_parts.append(f"ユーザー: {user_input}")

        full_prompt = "\n".join(prompt_parts)

        # (後続) トークン数確認＆制御はここに追加可能

        return full_prompt

    def update(self, user_input: str, model_output: str, actions: str = None):
        """
        入力・出力ログを保存
        """
        timestamp = datetime.now().strftime("%y%m%d_%H%M")

        # 入力保存
        with open(os.path.join(self.inputs_dir, f"{timestamp}_input.txt"), "w", encoding="utf-8") as f:
            f.write(user_input)

        # 出力保存
        output_text = model_output
        if actions:
            output_text += "\n\n【実行】\n" + actions

        with open(os.path.join(self.outputs_dir, f"{timestamp}_output.txt"), "w", encoding="utf-8") as f:
            f.write(output_text)


        # (Optional) 実行ログ保存も後で組み込める
