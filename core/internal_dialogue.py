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
            topic: 対話のトピック
            
        Returns:
            str: 初期応答
        """
        output_manager.output(
            f"\n=== 内的対話: 開始 ===",
            OutputType.INTERNAL_DIALOGUE
        )
        output_manager.output(
            f"トピック: {topic}",
            OutputType.INTERNAL_DIALOGUE
        )
        
        # 初期プロンプトの構築
        prompt = f"""
以下のトピックについて、自己改善と応答の最適化のための内的対話を開始します：

トピック: {topic}

あなたは、エージェントの応答方法を分析し、改善点を提案する役割を担っています。
以下の点について詳細に分析してください：

1. 機能と制約の理解
   - 利用可能な関数とその仕様の理解度
   - 各関数の使用制限と適切な使用場面
   - セーフモードやsandbox_pathなどの制約への対応

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

分析結果は具体的かつ実践的な提案を含めてください。
"""
        output_manager.output(
            "初期プロンプトを送信...",
            OutputType.INTERNAL_DIALOGUE
        )
        
        response = self.internal_client.ask(prompt)
        output_manager.output(
            f"初期応答: {response}",
            OutputType.INTERNAL_DIALOGUE
        )
        
        # 対話履歴に追加
        self.dialogue_history.append({
            "role": "internal",
            "content": response,
            "timestamp": datetime.now().isoformat()
        })
        
        return response

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