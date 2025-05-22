# core/internal_dialogue.py

from typing import Optional, Dict, Any, List
from api.client import LLMClient
import yaml
import os
from datetime import datetime
from utils.output_manager import output_manager, OutputType
from utils.config_loader import load_config

class InternalDialogueError(Exception):
    """内的対話システムのエラー基底クラス"""
    pass

class ConfigError(InternalDialogueError):
    """設定関連のエラー"""
    pass

class DialogueError(InternalDialogueError):
    """対話処理中のエラー"""
    pass

class InternalDialogue:
    """
    【InternalDialogueクラス】
    - エージェントと内部インスタンスの対話を管理
    - 自己改善や内省的な対話を実現
    - 設定可能な対話パラメータ（温度、履歴数など）
    """

    def __init__(self):
        try:
            # 設定ファイルの読み込み
            config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'config.yaml')
            output_manager.output(
                f"設定ファイルを読み込み中: {config_path}",
                OutputType.SYSTEM
            )
            self.config = load_config(config_path)
            
            # 内的対話設定の取得
            if 'recursion' not in self.config:
                raise ConfigError("recursionセクションが見つかりません")
            
            dialogue_config = self.config['recursion'].get('internal_dialogue')
            if not dialogue_config:
                raise ConfigError("internal_dialogue設定が見つかりません")
            
            if not dialogue_config.get('enabled', False):
                raise ConfigError("内的対話が無効化されています")
            
            client_name = dialogue_config.get('client')
            if not client_name or client_name not in self.config.get('clients', {}):
                raise ConfigError(f"無効なクライアント設定: {client_name}")
            
            # 内的対話用のLLMクライアントを初期化
            output_manager.output(
                f"LLMクライアントを初期化: {client_name}",
                OutputType.SYSTEM
            )
            self.internal_client = LLMClient(
                role=dialogue_config['roles']['internal']
            )
            
            self.dialogue_history: List[Dict[str, str]] = []
            self.max_history = dialogue_config['max_history']
            self.start_time = datetime.now()
            self.topic: Optional[str] = None
            self.is_active = False
            
            output_manager.output(
                "内的対話システムの初期化が完了しました",
                OutputType.SYSTEM
            )
            
        except Exception as e:
            output_manager.output(
                f"内的対話システムの初期化に失敗: {str(e)}",
                OutputType.SYSTEM
            )
            raise ConfigError(f"初期化エラー: {str(e)}")

    def start_dialogue(self, topic: str) -> str:
        """
        内的対話を開始する
        Args:
            topic (str): 対話のトピック
        Returns:
            str: 初期応答
        Raises:
            DialogueError: 対話開始に失敗した場合
        """
        try:
            if self.is_active:
                raise DialogueError("既に内的対話が進行中です")
            
            self.is_active = True
            self.topic = topic
            self.start_time = datetime.now()
            self.dialogue_history = []
            
            output_manager.output(
                f"内的対話を開始: トピック={topic}",
                OutputType.INTERNAL_DIALOGUE
            )
            prompt = f"""
【内的対話開始】
トピック: {topic}

あなたは、このエージェントの内部インスタンスとして、以下の役割を担います：
1. エージェントの応答や行動を客観的に分析
2. 改善点や代替案を提案
3. より良い応答方法を模索

まず、このトピックについて、あなたの見解を述べてください。
"""
            response = self.internal_client.ask(prompt)
            self.dialogue_history.append({"role": "system", "content": prompt})
            self.dialogue_history.append({"role": "internal", "content": response})
            
            output_manager.output(
                "内的対話の開始が完了しました",
                OutputType.INTERNAL_DIALOGUE,
                data={"prompt": prompt, "response": response},
                save_to_file=True
            )
            return response
            
        except Exception as e:
            self.is_active = False
            output_manager.output(
                f"内的対話の開始に失敗: {str(e)}",
                OutputType.SYSTEM
            )
            raise DialogueError(f"対話開始エラー: {str(e)}")

    def continue_dialogue(self, response: str) -> str:
        """
        内的対話を継続する
        Args:
            response (str): 分析対象の応答
        Returns:
            str: 分析結果
        Raises:
            DialogueError: 対話継続に失敗した場合
        """
        try:
            if not self.is_active:
                raise DialogueError("内的対話が開始されていません")
            
            if len(self.dialogue_history) >= self.max_history * 2:  # システムと内部の2倍
                output_manager.output(
                    "対話履歴が上限に達しました。古い履歴を削除します。",
                    OutputType.SYSTEM
                )
                self.dialogue_history = self.dialogue_history[-self.max_history*2:]
            
            prompt = f"""
【内的対話継続】
トピック: {self.topic}

前回の応答に対する分析をお願いします：
{response}

以下の観点から分析してください：
1. 応答の適切性
2. 改善できる点
3. 代替案の提案
"""
            analysis = self.internal_client.ask(prompt)
            self.dialogue_history.append({"role": "system", "content": prompt})
            self.dialogue_history.append({"role": "internal", "content": analysis})
            
            output_manager.output(
                "内的対話の継続が完了しました",
                OutputType.INTERNAL_DIALOGUE,
                data={"prompt": prompt, "analysis": analysis},
                save_to_file=True
            )
            return analysis
            
        except Exception as e:
            output_manager.output(
                f"内的対話の継続に失敗: {str(e)}",
                OutputType.SYSTEM
            )
            raise DialogueError(f"対話継続エラー: {str(e)}")

    def end_dialogue(self) -> Dict[str, Any]:
        """
        内的対話を終了し、結果を返す
        Returns:
            dict: 対話の結果サマリー
        """
        try:
            if not self.is_active:
                raise DialogueError("内的対話が開始されていません")
            
            duration = datetime.now() - self.start_time
            summary = {
                "topic": self.topic,
                "duration_seconds": duration.total_seconds(),
                "message_count": len(self.dialogue_history) // 2,  # システムと内部の2倍
                "is_active": False
            }
            
            self.is_active = False
            output_manager.output(
                f"内的対話を終了: {summary}",
                OutputType.INTERNAL_DIALOGUE,
                data=summary,
                save_to_file=True
            )
            return summary
            
        except Exception as e:
            output_manager.output(
                f"内的対話の終了に失敗: {str(e)}",
                OutputType.SYSTEM
            )
            raise DialogueError(f"対話終了エラー: {str(e)}")

    def get_state(self) -> Dict[str, Any]:
        """
        現在の内的対話の状態を取得
        Returns:
            dict: 内的対話の状態情報
        """
        return {
            "is_active": self.is_active,
            "topic": self.topic,
            "start_time": self.start_time,
            "message_count": len(self.dialogue_history) // 2,
            "max_history": self.max_history
        } 