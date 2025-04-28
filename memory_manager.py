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
        self.summary_combined_path = os.path.join(self.summaries_dir, "summary_combined.txt")

        # 設定読み込み
        config_path = os.path.join(os.getcwd(), "config.yaml")
        if not os.path.exists(config_path):
            raise FileNotFoundError("config.yamlが存在しません。プロジェクト直下に配置してください。")

        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        self.model_name = config.get("model", {}).get("name", "gemini-2.5-pro-exp-03-25")
        self.temperature = config.get("model", {}).get("temperature", 0.2)
        self.prompt_base_path = config.get("model", {}).get("prompt_base_path", "templates/prompt_base.md")

        self.recent_turns = config.get("memory", {}).get("recent_turns", 3)
        self.max_tokens = config.get("memory", {}).get("max_tokens", 100000)

        self.safe_mode = config.get("execution", {}).get("safe_mode", True)
        self.sandbox_path = config.get("execution", {}).get("sandbox_path", "sandbox/")

        self._ensure_dirs()

    def _ensure_dirs(self):
        """ 必要なディレクトリ群とsummary_combined.txtを作成 """
        for d in [self.inputs_dir, self.outputs_dir, self.summaries_dir]:
            os.makedirs(d, exist_ok=True)
        if not os.path.exists(self.summary_combined_path):
            with open(self.summary_combined_path, "w", encoding="utf-8") as f:
                f.write("")

    def _build_system_context(self) -> str:
        """ 現状の自己仕様＋長期記憶要約をまとめる """
        try:
            from functions_registry import FUNCTIONS
            function_names = ", ".join(FUNCTIONS.keys())

            if os.path.exists(self.summary_combined_path):
                with open(self.summary_combined_path, "r", encoding="utf-8") as f:
                    summary_text = f.read().strip()
            else:
                summary_text = "（まだ長期記憶はありません）"

            system_prompt = f"""【エージェント仕様】
- モデル: {self.model_name}
- 温度設定: {self.temperature}
- 使用可能関数: {function_names}
- メモリ: 最大トークン{self.max_tokens}, 直近履歴{self.recent_turns}ターン
- 実行モード: セーフモード({self.safe_mode}), sandbox_path={self.sandbox_path}

【長期記憶要約】
{summary_text}
"""
            return system_prompt

        except Exception as e:
            logging.error(f"自己仕様プロンプト生成エラー: {str(e)}")
            return "【エージェント仕様取得失敗】"

    def get_all_history(self):
        """ 過去のinputs/outputsをまとめて読み込み、履歴リスト化する """
        history = []
        input_files = sorted(os.listdir(self.inputs_dir))
        output_files = sorted(os.listdir(self.outputs_dir))

        for input_file, output_file in zip(input_files, output_files):
            try:
                with open(os.path.join(self.inputs_dir, input_file), "r", encoding="utf-8") as f_in:
                    user_text = f_in.read()
                with open(os.path.join(self.outputs_dir, output_file), "r", encoding="utf-8") as f_out:
                    model_text = f_out.read()

                history.append({"role": "user", "parts": [{"text": user_text}]})
                history.append({"role": "model", "parts": [{"text": model_text}]})
            except Exception as e:
                logging.error(f"履歴読み込みエラー: {input_file}, {output_file} -> {str(e)}")
                continue

        return history[-(self.recent_turns*2):]  # ユーザー・モデルセットで数える

    def build_prompt(self, user_input: str) -> str:
        """ 現在の履歴＋新規指示を組み合わせたプロンプトを構築 """
        system_context = self._build_system_context()
        history = self.get_all_history()
        prompt_parts = [system_context]

        # --- 履歴区切り線 ---
        prompt_parts.append("\n\n【履歴開始】\n" + "━━━━━━━━━━━━━━━━━━\n")

        # --- 過去の履歴追加 ---
        for h in history:
            role = h["role"]
            text = h["parts"][0]["text"]
            if role == "user":
                prompt_parts.append(f"あなたの指示> {text}")
            else:
                prompt_parts.append(f"エージェント> {text}")
            prompt_parts.append("────────────")  # 各ターンの間に区切り線

        # --- 新しい指示開始 ---
        prompt_parts.append("\n【現在ターン】\n" + "━━━━━━━━━━━━━━━━━━\n")
        prompt_parts.append(f"あなたの指示> {user_input}")

        full_prompt = "\n".join(prompt_parts)

        return full_prompt


    def update(self, user_input: str, model_output: str, actions: str = None):
        """ 入力・出力ログを保存 """
        timestamp = datetime.now().strftime("%y%m%d_%H%M")

        try:
            # 入力保存
            with open(os.path.join(self.inputs_dir, f"{timestamp}_input.txt"), "w", encoding="utf-8") as f:
                f.write(user_input)

            # 出力保存
            output_text = model_output
            if actions:
                output_text += "\n\n【実行】\n" + actions

            with open(os.path.join(self.outputs_dir, f"{timestamp}_output.txt"), "w", encoding="utf-8") as f:
                f.write(output_text)

            logging.info(f"履歴保存成功: {timestamp}")

        except Exception as e:
            logging.error(f"履歴保存エラー: {str(e)}")

    def save_summary(self, summary_text: str):
        """ 要約を追記保存 """
        try:
            with open(self.summary_combined_path, "a", encoding="utf-8") as f:
                f.write(summary_text + "\n")
            logging.info("要約追加保存完了")
        except Exception as e:
            logging.error(f"要約保存エラー: {str(e)}")
