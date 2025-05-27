# core/internal_dialogue.py

from typing import Optional, Dict, Any, List
from api.client import LLMClient
import yaml
import os
from datetime import datetime
from utils.output_manager import output_manager, OutputType
from utils.config_loader import load_config
from memory.memory_manager import MemoryManager
import logging

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
        self.output_manager = output_manager
        self.memory = MemoryManager()
        
        # 設定の読み込み
        try:
            # プロジェクトのルートディレクトリを取得
            root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            config_path = os.path.join(root_dir, "config", "config.yaml")
            
            self.output_manager.output(
                f"設定ファイルを読み込み中: {config_path}",
                OutputType.SYSTEM
            )
            
            with open(config_path, "r", encoding="utf-8") as f:
                self.config = yaml.safe_load(f)
                
            # 設定の検証
            if not isinstance(self.config, dict):
                raise ConfigError("設定ファイルの形式が不正です")
                
            if "recursion" not in self.config:
                raise ConfigError("recursionセクションが見つかりません")
                
            dialogue_config = self.config["recursion"].get("internal_dialogue", {})
            if not dialogue_config:
                raise ConfigError("internal_dialogue設定が見つかりません")
                
            # 必要な設定の取得
            self.max_iterations = dialogue_config.get("max_iterations", 3)
            self.temperature = dialogue_config.get("temperature", 0.7)
            self.topics = dialogue_config.get("topics", [])
            
            # LLMクライアントの初期化
            client_config = self.config.get("clients", {}).get("recur", {})
            if not client_config:
                raise ConfigError("recurクライアント設定が見つかりません")
                
            self.internal_client = LLMClient(
                role="recur"  # roleパラメータを使用
            )
            
        except Exception as e:
            self.output_manager.output(
                f"設定ファイルの読み込みに失敗: {str(e)}",
                OutputType.ERROR
            )
            # デフォルト設定を使用
            self.config = {
                "internal_dialogue": {
                    "max_iterations": 3,
                    "temperature": 0.7,
                    "topics": ["自己改善", "問題解決", "最適化", "分析", "検証"]
                }
            }
            self.max_iterations = 3
            self.temperature = 0.7
            self.topics = ["自己改善", "問題解決", "最適化", "分析", "検証"]
            
            # デフォルトのLLMクライアントを初期化
            self.internal_client = LLMClient(
                role="recur"  # roleパラメータを使用
            )
        
        # 内的対話の履歴
        self.dialogue_history = []
        
        self.output_manager.output(
            "内的対話システムを初期化しました",
            OutputType.SYSTEM
        )

    def start_dialogue(self, topic: str) -> str:
        """
        内的対話を開始する
        
        Args:
            topic: 対話のトピック
            
        Returns:
            str: 初期応答
        """
        # 内的対話の開始を記録
        self.output_manager.output(f"\n=== 内的対話: 開始 ===\nトピック: {topic}", OutputType.INTERNAL_DIALOGUE)
        self.output_manager.output("初期プロンプトを送信...", OutputType.INTERNAL_DIALOGUE)
        
        # 応答の取得
        response = self.internal_client.ask(f"""
以下のトピックについて、自己改善と応答の最適化のための内的対話を開始します：

トピック: {topic}

あなたは、エージェントの応答方法を分析し、改善点を提案する役割を担っています。
以下の点について詳細に分析してください：

1. 自己認識の深さ
   - 自身の機能と制約の理解度
   - 利用可能な関数の使用方法の把握
   - セーフモードなどの制約への対応

2. 応答の質と構造
   - 応答の明確さと正確性
   - 情報の構造化と整理
   - ユーザーへの配慮と共感

3. 自己改善の可能性
   - 内的対話システムの活用方法
   - 再帰処理の効果的な使用
   - 長期記憶の活用

4. 具体的な改善提案
   - 現在の応答方法の問題点
   - より効果的な応答方法の提案
   - 自己認識の向上方法

特に以下の点に注意して分析してください：
- 自身の機能と制約を正確に理解しているか
- 各関数の使用方法を適切に把握しているか
- 制約条件を考慮した応答ができているか
- 自己改善のための内的対話を効果的に活用できているか

分析結果は具体的かつ実践的な提案を含めてください。
""")
        
        # 対話履歴に追加
        self.dialogue_history.append({
            "role": "internal",
            "content": response,
            "timestamp": datetime.now().isoformat()
        })
        
        # 応答を整形して返す
        formatted_response = f"【内的対話の分析結果】\n{response}"
        # 内容はINFOログに記録
        logging.info(f"内的対話応答: {formatted_response}")
        return formatted_response

    def continue_dialogue(self, response: str) -> str:
        """
        内的対話を継続する
        
        Args:
            response: 前回の応答
            
        Returns:
            str: 分析結果
        """
        output_manager.output(
            f"\n=== 内的対話: 継続 ===",
            OutputType.INTERNAL_DIALOGUE
        )
        output_manager.output(
            f"前回の応答: {response}",
            OutputType.INTERNAL_DIALOGUE
        )
        
        # 継続プロンプトの構築
        prompt = f"""
前回の応答に対する分析を続けます：

応答: {response}

以下の観点から、この応答を分析し、具体的な改善提案をしてください：

1. 応答の質と適切性
2. ユーザー体験の向上点
3. コミュニケーションの改善点
4. 技術的な改善提案

分析結果を具体的に示してください。
"""
        output_manager.output(
            "分析プロンプトを送信...",
            OutputType.INTERNAL_DIALOGUE
        )
        
        analysis = self.internal_client.ask(prompt)
        output_manager.output(
            f"分析結果: {analysis}",
            OutputType.INTERNAL_DIALOGUE
        )
        
        # 対話履歴に追加
        self.dialogue_history.append({
            "role": "internal",
            "content": analysis,
            "timestamp": datetime.now().isoformat()
        })
        
        return analysis

    def end_dialogue(self) -> Dict[str, Any]:
        """
        内的対話を終了し、サマリーを生成する
        
        Returns:
            Dict[str, Any]: 対話のサマリー
        """
        output_manager.output(
            f"\n=== 内的対話: 終了 ===",
            OutputType.INTERNAL_DIALOGUE
        )
        
        # サマリープロンプトの構築
        dialogue_text = "\n".join([
            f"{entry['role']}: {entry['content']}"
            for entry in self.dialogue_history
        ])
        
        prompt = f"""
以下の内的対話のサマリーを生成してください：

{dialogue_text}

以下の項目を含むサマリーを生成してください：

1. 主な分析結果
2. 重要な改善提案
3. 全体的な評価
4. 今後の推奨事項

サマリーは簡潔かつ具体的に示してください。
"""
        output_manager.output(
            "サマリー生成プロンプトを送信...",
            OutputType.INTERNAL_DIALOGUE
        )
        
        summary = self.internal_client.ask(prompt)
        output_manager.output(
            f"対話サマリー: {summary}",
            OutputType.INTERNAL_DIALOGUE
        )
        
        # サマリー情報の構築
        summary_info = {
            "topic": "自己改善と応答の最適化",
            "duration_seconds": (datetime.now() - self.dialogue_history[0]["timestamp"]).total_seconds(),
            "message_count": len(self.dialogue_history),
            "summary": summary,
            "is_active": False
        }
        
        output_manager.output(
            f"対話統計:",
            OutputType.INTERNAL_DIALOGUE
        )
        output_manager.output(
            f"- メッセージ数: {summary_info['message_count']}",
            OutputType.INTERNAL_DIALOGUE
        )
        output_manager.output(
            f"- 所要時間: {summary_info['duration_seconds']:.1f}秒",
            OutputType.INTERNAL_DIALOGUE
        )
        
        return summary_info

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