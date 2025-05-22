import time
import logging
import yaml
from typing import Dict, Any, Optional, Callable, List
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path

# 関数トリガー状態を共有する辞書（recursion_flag）をインポート
from core.functions_registry import recursion_flag

# 内的対話システムをインポート
from core.internal_dialogue import InternalDialogue, DialogueError
from utils.output_manager import output_manager, OutputType
from utils.config_loader import load_config

@dataclass
class RecursionState:
    """再帰処理の状態を管理するクラス"""
    count: int = 0
    is_active: bool = False
    last_error: Optional[str] = None
    dialogue_history: List[Dict[str, Any]] = None
    last_triggered: Optional[datetime] = None

    def __post_init__(self):
        if self.dialogue_history is None:
            self.dialogue_history = []

class RecursionManager:
    """
    【RecursionManagerクラス】
    - 内的対話システムのラッパーとして機能
    - 自己改善や内省的な対話を実現
    - 設定可能な対話パラメータ（温度、履歴数など）
    """

    def __init__(self, main_func: Callable):
        """
        コンストラクタ

        Args:
            main_func (callable): 再帰時に呼び出すメイン関数（通常はmain.main関数）
        """
        self.main_func = main_func
        self.state = RecursionState()
        self.state.context = {}
        self.state.dialogue_history = []
        self.dialogue = None

        # 設定ファイルの読み込み（再帰関連設定）
        try:
            config = load_config("config/config.yaml")
            recursion_config = config.get("recursion", {})
            
            self.max_recursions = recursion_config.get("max_recursions", 3)
            self.recursion_delay = recursion_config.get("recursion_delay", 2.0)
            self.enabled = recursion_config.get("enabled", True)
            self.retry_count = recursion_config.get("retry_count", 3)
            
            output_manager.output(
                f"再帰設定を読み込み: max={self.max_recursions}, delay={self.recursion_delay}s, retry={self.retry_count}",
                OutputType.SYSTEM
            )
        except Exception as e:
            output_manager.output(
                f"設定ファイル読み込みエラー: {e}",
                OutputType.SYSTEM
            )
            self.max_recursions = 3
            self.recursion_delay = 2.0
            self.enabled = False
            self.retry_count = 3

    def should_retry(self) -> bool:
        """
        再帰処理を再試行すべきか判断する
        """
        if not self.state.last_error:
            return False
        
        # エラーの種類に基づいて再試行を判断
        error_lower = self.state.last_error.lower()
        retry_conditions = [
            "timeout",
            "connection",
            "dialogue",
            "internal"
        ]
        
        return any(condition in error_lower for condition in retry_conditions)

    def handle_recursion(self):
        """
        内的対話を開始し、必要に応じて再帰的に処理を実行
        """
        if not self.enabled:
            output_manager.output(
                "再帰処理が無効化されています",
                OutputType.SYSTEM
            )
            return

        self.state.is_active = True
        self.state.last_triggered = datetime.now()

        try:
            # 内的対話システムを初期化
            self.dialogue = InternalDialogue()
            
            # 内的対話を開始
            topic = "自己改善と応答の最適化"
            response = self.dialogue.start_dialogue(topic)
            output_manager.output(
                f"内的対話を開始: {topic}",
                OutputType.INTERNAL_DIALOGUE,
                data={"topic": topic, "response": response},
                save_to_file=True
            )
            
            while self.state.count < self.max_recursions:
                # 再帰の前に指定秒数の遅延（負荷軽減 or 時間調整用）
                time.sleep(self.recursion_delay)

                try:
                    # main() を再帰的に呼び出す（引数で recurse=True を明示）
                    self.main_func(recurse=True)
                    
                    # 内的対話を継続
                    analysis = self.dialogue.continue_dialogue(response)
                    output_manager.output(
                        "内的対話を継続: 分析結果を取得",
                        OutputType.INTERNAL_DIALOGUE,
                        data={"response": response, "analysis": analysis},
                        save_to_file=True
                    )
                    
                    self.state.count += 1
                    self.state.last_error = None

                except Exception as e:
                    error_msg = f"再帰ターン中エラー発生: {str(e)}"
                    output_manager.output(
                        error_msg,
                        OutputType.SYSTEM
                    )
                    self.state.last_error = error_msg
                    
                    # 再試行可能なエラーの場合、リトライ
                    if self.should_retry() and self.state.count < self.retry_count:
                        output_manager.output(
                            f"再帰処理を再試行します（{self.state.count + 1}/{self.retry_count}）",
                            OutputType.SYSTEM
                        )
                        continue
                    break

        except DialogueError as e:
            error_msg = f"内的対話エラー: {str(e)}"
            output_manager.output(
                error_msg,
                OutputType.SYSTEM
            )
            self.state.last_error = error_msg
        except Exception as e:
            error_msg = f"予期せぬエラー: {str(e)}"
            output_manager.output(
                error_msg,
                OutputType.SYSTEM
            )
            self.state.last_error = error_msg
        finally:
            self.state.is_active = False
            if self.dialogue:
                try:
                    summary = self.dialogue.end_dialogue()
                    output_manager.output(
                        f"内的対話を終了: {summary}",
                        OutputType.INTERNAL_DIALOGUE,
                        data={"summary": summary},
                        save_to_file=True
                    )
                except Exception as e:
                    output_manager.output(
                        f"内的対話の終了に失敗: {str(e)}",
                        OutputType.SYSTEM
                    )
            output_manager.output(
                f"再帰処理終了: 実行回数={self.state.count}, 最終エラー={self.state.last_error}",
                OutputType.SYSTEM
            )

    def get_state(self) -> Dict[str, Any]:
        """
        現在の再帰状態を取得
        Returns:
            dict: 再帰状態の情報
        """
        state = {
            "count": self.state.count,
            "max_recursions": self.max_recursions,
            "last_triggered": self.state.last_triggered,
            "last_error": self.state.last_error,
            "is_active": self.state.is_active,
            "context": self.state.context,
            "dialogue_history": self.state.dialogue_history
        }
        
        if self.dialogue:
            state["dialogue"] = self.dialogue.get_state()
            
        return state

    def reset_state(self):
        """再帰状態をリセット"""
        self.state = RecursionState()
        self.state.context = {}
        self.state.dialogue_history = []
        self.dialogue = None
        output_manager.output(
            "再帰状態をリセットしました",
            OutputType.SYSTEM
        ) 