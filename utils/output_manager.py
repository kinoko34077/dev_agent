import logging
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Union
from enum import Enum, auto
import os
from utils.helpers import get_timestamp
from utils.config_loader import get_debug_mode

class OutputType(Enum):
    SYSTEM = "system"           # システムログ
    USER = "user"              # ユーザー入力
    AGENT = "agent"            # エージェント応答
    INTERNAL_DIALOGUE = "internal"  # 内的対話
    ERROR = "error"            # エラー
    FUNCTION = "function"      # 関数実行
    DEBUG = "debug"            # デバッグ情報

class OutputManager:
    """
    ログ出力を一元管理するクラス
    - 標準出力とログファイルの両方に対応
    - 出力タイプに応じたフォーマット制御
    - 内的対話履歴の保存
    """
    
    def __init__(self):
        self.log_dir = "logs"
        self.memory_dir = "memory"
        self._ensure_dirs()
        self._setup_log_files()
        self.debug_mode = get_debug_mode()
        
        # 出力タイプごとのフォーマット設定
        self.formats = {
            OutputType.USER: {
                "prefix": "🗣",
                "log_level": logging.INFO
            },
            OutputType.AGENT: {
                "prefix": "🤖",
                "log_level": logging.INFO
            },
            OutputType.INTERNAL_DIALOGUE: {
                "prefix": "💭",
                "log_level": logging.INFO
            },
            OutputType.ERROR: {
                "prefix": "❌",
                "log_level": logging.ERROR
            },
            OutputType.FUNCTION: {
                "prefix": "🔧",
                "log_level": logging.INFO
            },
            OutputType.DEBUG: {
                "prefix": "🔍",
                "log_level": logging.DEBUG
            }
        }
        
        # 内的対話履歴
        self.dialogue_history = []
        
    def _ensure_dirs(self):
        """必要なディレクトリを作成"""
        dirs = [
            self.log_dir,
            os.path.join(self.memory_dir, "inputs"),
            os.path.join(self.memory_dir, "outputs"),
            os.path.join(self.memory_dir, "context"),
            os.path.join(self.memory_dir, "summaries")
        ]
        for d in dirs:
            os.makedirs(d, exist_ok=True)

    def _setup_log_files(self):
        """ログファイルの初期設定"""
        timestamp = get_timestamp()
        self.current_log = os.path.join(self.log_dir, f"{timestamp}_system.log")
        self.current_memory = {
            "input": os.path.join(self.memory_dir, "inputs", f"{timestamp}_input.txt"),
            "output": os.path.join(self.memory_dir, "outputs", f"{timestamp}_output.txt"),
            "context": os.path.join(self.memory_dir, "context", f"{timestamp}_context.txt"),
            "summary": os.path.join(self.memory_dir, "summaries", f"{timestamp}_summary.txt")
        }

    def output(self, message: str, output_type: OutputType) -> None:
        """
        メッセージを出力し、必要に応じてメモリに保存します。
        
        Args:
            message: 出力するメッセージ
            output_type: 出力タイプ（USER=本文, AGENT=返答）
        """
        # 標準メッセージのリスト（画面表示のみ）
        standard_messages = ["入力受信", "応答生成完了"]
        
        # 画面表示
        if message in standard_messages:
            print(message)
            return
            
        # 本文と返答の場合のみメモリに保存
        if output_type in [OutputType.USER, OutputType.AGENT]:
            try:
                file_path = self.current_memory["input"] if output_type == OutputType.USER else self.current_memory["output"]
                with open(file_path, "a", encoding="utf-8") as f:
                    f.write(f"{message}\n")
            except Exception as e:
                print(f"メモリ保存エラー: {str(e)}")
        
        # ログファイルには全て記録
        self._save_to_log(message, output_type)

    def _get_prefix(self, output_type: OutputType) -> str:
        """出力タイプに応じたプレフィックスを返す"""
        prefixes = {
            OutputType.SYSTEM: "⚙️",
            OutputType.USER: "🗣",
            OutputType.AGENT: "🤖",
            OutputType.INTERNAL_DIALOGUE: "💭",
            OutputType.ERROR: "❌",
            OutputType.FUNCTION: "🔧",
            OutputType.DEBUG: "🔍"
        }
        return prefixes.get(output_type, "•")

    def _save_to_log(self, message: str, output_type: OutputType):
        """ログファイルに保存"""
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_entry = f"{timestamp} - {output_type.value.upper()} - {message}\n"
            
            with open(self.current_log, "a", encoding="utf-8") as f:
                f.write(log_entry)
        except Exception as e:
            print(f"ログ保存エラー: {str(e)}")

    def save_context(self, context: str):
        """コンテキストを保存"""
        try:
            with open(self.current_memory["context"], "w", encoding="utf-8") as f:
                f.write(context)
        except Exception as e:
            print(f"コンテキスト保存エラー: {str(e)}")

    def save_summary(self, summary: str):
        """要約を保存"""
        try:
            with open(self.current_memory["summary"], "w", encoding="utf-8") as f:
                f.write(summary)
        except Exception as e:
            print(f"要約保存エラー: {str(e)}")

    def _save_dialogue_history(self) -> None:
        """内的対話履歴をファイルに保存"""
        if not self.dialogue_history:
            return
            
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = self.memory_dir / f"dialogue_{timestamp}.json"
        
        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(self.dialogue_history, f, ensure_ascii=False, indent=2)
            print(f"内的対話履歴を保存: {filename}")
        except Exception as e:
            print(f"内的対話履歴の保存に失敗: {str(e)}")
    
    def clear_history(self) -> None:
        """内的対話履歴をクリア"""
        self.dialogue_history = []
        print("内的対話履歴をクリアしました")

# グローバルインスタンス
output_manager = OutputManager() 