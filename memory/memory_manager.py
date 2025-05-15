# memory/memory_manager.py

import os
import yaml
import json
import logging
from datetime import datetime

# 関数名一覧の取得に使用
from core.functions_registry import FUNCTIONS

class MemoryManager:
    """
    【MemoryManagerクラス】
    - エージェントの入出力履歴（inputs/outputs）および長期記憶（summaries）を管理
    - 履歴の保存・要約の追加・プロンプト構築・直近履歴抽出などを担当
    """

    def __init__(self):
        # 各種ディレクトリ構成（保存場所）
        self.memory_dir = "memory"
        self.inputs_dir = os.path.join(self.memory_dir, "inputs")
        self.outputs_dir = os.path.join(self.memory_dir, "outputs")
        self.summaries_dir = os.path.join(self.memory_dir, "summaries")
        self.summary_combined_path = os.path.join(self.summaries_dir, "summary_combined.txt")

        # 設定ファイル読み込み（config.yaml）
        config_path = os.path.join(os.getcwd(), "config/config.yaml")
        if not os.path.exists(config_path):
            raise FileNotFoundError("config.yaml が存在しません。config/ に配置してください。")

        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        # モデル設定・プロンプトパスなど
        self.model_name = config.get("model", {}).get("name", "gemini-2.5-pro-exp-03-25")
        self.temperature = config.get("model", {}).get("temperature", 0.2)
        self.prompt_base_path = config.get("model", {}).get("prompt_base_path", "templates/prompt_base.md")

        # メモリ設定
        self.recent_turns = config.get("memory", {}).get("recent_turns", 3)
        self.max_tokens = config.get("memory", {}).get("max_tokens", 100000)

        # 実行設定
        self.safe_mode = config.get("execution", {}).get("safe_mode", True)
        self.sandbox_path = config.get("execution", {}).get("sandbox_path", "sandbox/")

        self._ensure_dirs()

    def _ensure_dirs(self):
        """
        必要なディレクトリとsummaryファイルの作成確認
        """
        for d in [self.inputs_dir, self.outputs_dir, self.summaries_dir]:
            os.makedirs(d, exist_ok=True)

        if not os.path.exists(self.summary_combined_path):
            with open(self.summary_combined_path, "w", encoding="utf-8") as f:
                f.write("")

    def _build_system_context(self) -> str:
        """
        自己仕様（モデル設定や使用可能関数、長期記憶の要約）を生成する。

        Returns:
            str: システムコンテキスト（プロンプト先頭に埋め込む）
        """
        try:
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
        """
        全履歴（inputs/outputs）を時系列で結合し、LLM向けフォーマットに変換

        Returns:
            list: 各発話を {"role": "user"/"model", "parts": [{"text": "..." }]} の形で格納
        """
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

        return history[-(self.recent_turns * 2):]

    def get_recent_history(self, recent_turns: int):
        """
        指定した直近ターン数（user→model）を返す

        Args:
            recent_turns (int): 直近のターン数（1ターン=発話+応答）

        Returns:
            list: 該当する履歴リスト
        """
        history = self.get_all_history()
        return history[-recent_turns * 2:]

    def build_prompt(self, user_input: str) -> str:
        """
        システムプロンプト＋履歴＋ユーザー入力から完全なプロンプトを構築

        Args:
            user_input (str): ユーザーからの新しい入力

        Returns:
            str: 組み立て済みプロンプト文字列
        """
        system_context = self._build_system_context()
        history = self.get_all_history()
        prompt_parts = [system_context]

        prompt_parts.append("\n\n【履歴開始】\n" + "━━━━━━━━━━━━━━━━━━\n")
        for h in history:
            role = h["role"]
            text = h["parts"][0]["text"]
            if role == "user":
                prompt_parts.append(f"あなたの指示> {text}")
            else:
                prompt_parts.append(f"エージェント> {text}")
            prompt_parts.append("────────────")

        prompt_parts.append("\n【現在ターン】\n" + "━━━━━━━━━━━━━━━━━━\n")
        prompt_parts.append(f"あなたの指示> {user_input}")

        return "\n".join(prompt_parts)

    def update(self, user_input: str, model_output: str, actions: str = None):
        """
        入出力データを個別のファイルに保存し、履歴を蓄積

        Args:
            user_input (str): 入力文字列
            model_output (str): モデル出力
            actions (str, optional): 【実行】ブロック（あれば追記）
        """
        timestamp = datetime.now().strftime("%y%m%d_%H%M")

        try:
            with open(os.path.join(self.inputs_dir, f"{timestamp}_input.txt"), "w", encoding="utf-8") as f:
                f.write(user_input)

            output_text = model_output
            if actions:
                output_text += "\n\n【実行】\n" + actions

            with open(os.path.join(self.outputs_dir, f"{timestamp}_output.txt"), "w", encoding="utf-8") as f:
                f.write(output_text)

            logging.info(f"履歴保存成功: {timestamp}")

        except Exception as e:
            logging.error(f"履歴保存エラー: {str(e)}")

    def save_summary(self, summary_text: str):
        """
        summary_combined.txt に要約を追記保存

        Args:
            summary_text (str): 追加する要約テキスト
        """
        try:
            with open(self.summary_combined_path, "a", encoding="utf-8") as f:
                f.write(summary_text + "\n")
            logging.info("要約追加保存完了")
        except Exception as e:
            logging.error(f"要約保存エラー: {str(e)}")
