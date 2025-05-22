# core/internal_dialogue.py

import logging
from typing import Optional, Dict, Any
from api.client import LLMClient

class InternalDialogue:
    """
    【InternalDialogueクラス】
    - エージェントと内部インスタンスの対話を管理
    - 自己改善や内省的な対話を実現
    - 設定可能な対話パラメータ（温度、履歴数など）
    """

    def __init__(self):
        # 内的対話用のLLMクライアントを初期化
        self.internal_client = LLMClient(role="recur")  # recurロールを使用
        self.dialogue_history = []
        self.max_history = 3  # 保持する対話履歴の最大数

    def start_dialogue(self, topic: str) -> str:
        """
        内的対話を開始し、最初の応答を取得

        Args:
            topic (str): 対話のトピック（例：「応答方法の改善」）

        Returns:
            str: 内部インスタンスからの応答
        """
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
        return response

    def continue_dialogue(self, response: str) -> str:
        """
        内的対話を継続し、次の応答を取得

        Args:
            response (str): エージェントからの応答

        Returns:
            str: 内部インスタンスからの応答
        """
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
        
        return internal_response

    def get_dialogue_summary(self) -> str:
        """
        現在までの内的対話の要約を取得

        Returns:
            str: 対話の要約
        """
        if not self.dialogue_history:
            return "内的対話はまだ開始されていません。"
        
        summary_prompt = f"""
【内的対話要約】
これまでの対話履歴を要約してください：

{self.dialogue_history}

重要なポイントと改善提案を簡潔にまとめてください。
"""
        return self.internal_client.ask(summary_prompt) 