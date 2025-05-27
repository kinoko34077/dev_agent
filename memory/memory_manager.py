# memory/memory_manager.py

import os
import logging
from datetime import datetime

from core.functions_registry import REGISTERED_FUNCTIONS
from utils.config_loader import load_config
from utils.fileio import write_file
from utils.secure_check import check_permission

class MemoryManager:
    def __init__(self):
        self.memory_dir = "memory"
        self.inputs_dir = os.path.join(self.memory_dir, "inputs")
        self.outputs_dir = os.path.join(self.memory_dir, "outputs")
        self.summaries_dir = os.path.join(self.memory_dir, "summaries")
        self.summary_combined_path = os.path.join(self.summaries_dir, "summary_combined.txt")

        config = load_config()

        self.model_name = config.get("model", {}).get("name", "gemini-2.5-pro-exp-03-25")
        self.temperature = config.get("model", {}).get("temperature", 0.2)
        self.prompt_base_path = config.get("model", {}).get("prompt_base_path", "templates/prompt_base.md")
        self.recent_turns = config.get("memory", {}).get("recent_turns", 3)
        self.max_tokens = config.get("memory", {}).get("max_tokens", 100000)
        self.safe_mode = config.get("execution", {}).get("safe_mode", True)
        self.sandbox_path = config.get("execution", {}).get("sandbox_path", "sandbox/")

        self._ensure_dirs()

    def _ensure_dirs(self):
        """必要なディレクトリを作成し、権限変更は行わない"""
        try:
            os.makedirs(self.memory_dir, exist_ok=True)
            for d in [self.inputs_dir, self.outputs_dir, self.summaries_dir]:
                os.makedirs(d, exist_ok=True)
            if not os.path.exists(self.summary_combined_path):
                write_file(self.summary_combined_path, "", "txt")
        except Exception as e:
            logging.error(f"ディレクトリ作成エラー: {str(e)}")
            raise

    def _build_system_context(self) -> str:
        try:
            # 関数情報の詳細な収集
            function_details = []
            for name, func_info in REGISTERED_FUNCTIONS.items():
                metadata = func_info.metadata
                schema = func_info.schema
                function_details.append(
                    f"- {name}: {metadata.description}\n"
                    f"  タグ: {', '.join(metadata.tags)}\n"
                    f"  引数: {schema}\n"
                    f"  GPT呼び出し: {'許可' if metadata.allow_gpt_call else '禁止'}"
                )

            if os.path.exists(self.summary_combined_path):
                with open(self.summary_combined_path, "r", encoding="utf-8") as f:
                    summary_text = f.read().strip()
            else:
                summary_text = "（まだ長期記憶はありません）"

            return f"""【エージェントの自己認識】

私は、Google Gemini APIを活用した自律型エージェントです。以下の特徴と機能を持っています：

1. 基本機能
- モデル: {self.model_name}
- 温度設定: {self.temperature}
- メモリ: 最大トークン{self.max_tokens}, 直近履歴{self.recent_turns}ターン
- 実行モード: セーフモード({self.safe_mode}), sandbox_path={self.sandbox_path}

2. 内的対話システム
- 自己分析と改善提案のための構造化された対話機能
- 再帰処理による自己改善の実現
- 長期記憶と短期記憶の統合

3. 利用可能な関数
{chr(10).join(function_details)}

4. 制約と注意点
- セーフモードでは、特定の操作が制限されます
- 各関数には使用制限と権限があります
- メモリとトークン数の制限があります

5. 自己改善の方法
- 内的対話システムを通じた自己分析
- 再帰処理による応答の最適化
- 長期記憶の活用による学習

【長期記憶要約】
{summary_text}

【重要な自己認識】
- 私は自律型エージェントとして、上記の機能と制約を理解しています
- 各関数の仕様と使用制限を把握し、適切に使用します
- 内的対話システムを通じて継続的に自己改善を行います
- ユーザーとの対話において、自分の機能と制約を考慮した応答を心がけます
"""
        except Exception as e:
            logging.error(f"自己仕様プロンプト生成エラー: {str(e)}")
            return "【エージェント仕様取得失敗】"

    def get_all_history(self):
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
        return self.get_all_history()[-recent_turns * 2:]

    def build_prompt(self, user_input: str) -> str:
        """
        プロンプトを構築する
        
        Args:
            user_input: ユーザーからの入力テキスト
            
        Returns:
            str: 構築されたプロンプト
        """
        try:
            # プロンプトベースの読み込み
            with open(self.prompt_base_path, "r", encoding="utf-8") as f:
                prompt_base = f.read()
            
            # システムコンテキストの構築
            system_context = self._build_system_context()
            
            # 履歴の取得
            history = self.get_recent_history(self.recent_turns)
            
            # プロンプトの構築
            prompt_parts = [
                prompt_base,  # プロンプトベース
                "\n\n【システムコンテキスト】\n━━━━━━━━━━━━━━━━━━",
                system_context,
                "\n\n【履歴開始】\n━━━━━━━━━━━━━━━━━━"
            ]
            
            # 履歴の追加
            for h in history:
                role = h["role"]
                text = h["parts"][0]["text"]
                prompt_parts.append(f"{'あなたの指示' if role == 'user' else 'エージェント'}> {text}")
                prompt_parts.append("────────────")
            
            # 現在のターンの追加
            prompt_parts.extend([
                "\n【現在ターン】\n━━━━━━━━━━━━━━━━━━",
                f"あなたの指示> {user_input}"
            ])
            
            return "\n".join(prompt_parts)
            
        except Exception as e:
            logging.error(f"プロンプト構築エラー: {str(e)}")
            return f"【エラー】プロンプト構築に失敗しました: {str(e)}\n\nあなたの指示> {user_input}"

    def update(self, user_input: str, model_output: str, actions: str = None):
        """履歴を更新する"""
        timestamp = datetime.now().strftime("%y%m%d_%H%M")
        try:
            self._ensure_dirs()
            input_path = os.path.join(self.inputs_dir, f"{timestamp}_input.txt")
            output_path = os.path.join(self.outputs_dir, f"{timestamp}_output.txt")
            # 権限チェック
            if not check_permission(input_path, 'write'):
                logging.error(f"書き込み権限がありません: {input_path}")
                return
            if not check_permission(output_path, 'write'):
                logging.error(f"書き込み権限がありません: {output_path}")
                return
            write_file(input_path, user_input, "txt")
            output_text = model_output
            if actions:
                output_text += "\n\n【実行】\n" + actions
            write_file(output_path, output_text, "txt")
            logging.info(f"履歴保存成功: {timestamp}")
        except Exception as e:
            error_msg = f"履歴保存エラー: {str(e)}"
            logging.error(error_msg)
            return

    def save_summary(self, summary_text: str):
        try:
            with open(self.summary_combined_path, "a", encoding="utf-8") as f:
                f.write(summary_text + "\n")
            logging.info("要約追加保存完了")
        except Exception as e:
            logging.error(f"要約保存エラー: {str(e)}")
