# core/internal_dialogue.py

import logging
from typing import Optional, Dict, Any
from api.client import LLMClient
import yaml
import os

logger = logging.getLogger(__name__)

class InternalDialogueError(Exception):
    """内的対話システムのエラー基底クラス"""
    pass

class ConfigError(InternalDialogueError):
    """設定関連のエラー"""
    pass

class DialogueError(InternalDialogueError):
    """対話処理関連のエラー"""
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
            logger.info(f"設定ファイルを読み込み中: {config_path}")
            
            with open(config_path, 'r', encoding='utf-8') as f:
                self.config = yaml.safe_load(f)
            
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
            logger.info(f"LLMクライアントを初期化: {client_name}")
            self.internal_client = LLMClient(
                role=dialogue_config['roles']['internal']
            )
            
            self.dialogue_history = []
            self.max_history = dialogue_config['max_history']
            logger.info("内的対話システムの初期化が完了しました")
            
        except yaml.YAMLError as e:
            logger.error(f"設定ファイルの解析に失敗: {str(e)}")
            raise ConfigError(f"設定ファイルの解析に失敗: {str(e)}")
        except FileNotFoundError as e:
            logger.error(f"設定ファイルが見つかりません: {str(e)}")
            raise ConfigError(f"設定ファイルが見つかりません: {str(e)}")
        except Exception as e:
            logger.error(f"内的対話システムの初期化に失敗: {str(e)}")
            raise InternalDialogueError(f"内的対話システムの初期化に失敗: {str(e)}")

    def start_dialogue(self, topic: str) -> str:
        """
        内的対話を開始し、最初の応答を取得

        Args:
            topic (str): 対話のトピック（例：「応答方法の改善」）

        Returns:
            str: 内部インスタンスからの応答

        Raises:
            DialogueError: 対話処理中にエラーが発生した場合
        """
        try:
            if not self.config or 'recursion' not in self.config or 'internal_dialogue' not in self.config['recursion']:
                raise ConfigError("設定が不足しています")
            logger.info(f"内的対話を開始: トピック={topic}")
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
            logger.info("内的対話の開始が完了しました")
            return response
        except ConfigError as ce:
            logger.error(f"設定エラー: {str(ce)}")
            raise DialogueError(f"設定エラー: {str(ce)}")
        except Exception as e:
            logger.error(f"内的対話の開始に失敗: {str(e)}")
            raise DialogueError(f"内的対話の開始に失敗: {str(e)}")

    def continue_dialogue(self, response: str) -> str:
        """
        内的対話を継続し、次の応答を取得

        Args:
            response (str): エージェントからの応答

        Returns:
            str: 内部インスタンスからの応答

        Raises:
            DialogueError: 対話処理中にエラーが発生した場合
        """
        try:
            logger.info("内的対話を継続")
            prompt = f"""
【内的対話継続】
エージェントの応答: {response}

この応答について、以下の観点から分析してください：
1. 応答の適切性
2. 改善できる点
3. より良い応答方法の提案

あなたの分析と提案を述べてください。
"""
            internal_response = self.internal_client.ask(prompt)
            
            # 履歴を更新
            self.dialogue_history.append({"role": "agent", "content": response})
            self.dialogue_history.append({"role": "internal", "content": internal_response})
            
            # 履歴が長すぎる場合は古いものを削除
            if len(self.dialogue_history) > self.max_history * 2:
                self.dialogue_history = self.dialogue_history[-self.max_history * 2:]
                logger.debug(f"対話履歴を{self.max_history * 2}件に制限しました")
            
            logger.info("内的対話の継続が完了しました")
            return internal_response
            
        except Exception as e:
            logger.error(f"内的対話の継続に失敗: {str(e)}")
            raise DialogueError(f"内的対話の継続に失敗: {str(e)}")

    def get_dialogue_summary(self) -> str:
        """
        現在までの内的対話の要約を取得

        Returns:
            str: 対話の要約

        Raises:
            DialogueError: 要約処理中にエラーが発生した場合
        """
        try:
            if not self.dialogue_history:
                logger.info("内的対話はまだ開始されていません")
                return "内的対話はまだ開始されていません。"
            
            logger.info("内的対話の要約を生成")
            summary_prompt = f"""
【内的対話要約】
これまでの対話履歴を要約してください：

{self.dialogue_history}

重要なポイントと改善提案を簡潔にまとめてください。
"""
            summary = self.internal_client.ask(summary_prompt)
            logger.info("内的対話の要約が完了しました")
            return summary
            
        except Exception as e:
            logger.error(f"内的対話の要約に失敗: {str(e)}")
            raise DialogueError(f"内的対話の要約に失敗: {str(e)}") 