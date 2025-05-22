"""
テストデータ生成ユーティリティモジュール
"""

from typing import Dict, Any, List
from datetime import datetime

class TestData:
    """テストデータ生成クラス"""
    
    @staticmethod
    def create_dialogue_history(count: int = 1) -> List[Dict[str, str]]:
        """
        対話履歴のテストデータを生成
        
        Args:
            count: 生成する対話の数
            
        Returns:
            List[Dict[str, str]]: 対話履歴
        """
        history = []
        for i in range(count):
            history.extend([
                {
                    "role": "system",
                    "content": f"システムメッセージ {i}"
                },
                {
                    "role": "internal",
                    "content": f"内部応答 {i}"
                }
            ])
        return history
    
    @staticmethod
    def create_dialogue_summary() -> Dict[str, Any]:
        """
        対話サマリーのテストデータを生成
        
        Returns:
            Dict[str, Any]: 対話サマリー
        """
        return {
            "topic": "テストトピック",
            "duration_seconds": 1.0,
            "message_count": 2,
            "is_active": False,
            "start_time": datetime.now().isoformat(),
            "end_time": datetime.now().isoformat()
        }
    
    @staticmethod
    def create_recursion_state() -> Dict[str, Any]:
        """
        再帰状態のテストデータを生成
        
        Returns:
            Dict[str, Any]: 再帰状態
        """
        return {
            "count": 0,
            "is_active": False,
            "last_error": None,
            "dialogue_history": [],
            "last_triggered": datetime.now().isoformat()
        }
    
    @staticmethod
    def create_error_response() -> Dict[str, str]:
        """
        エラーレスポンスのテストデータを生成
        
        Returns:
            Dict[str, str]: エラーレスポンス
        """
        return {
            "error": "テストエラー",
            "message": "エラーメッセージ",
            "timestamp": datetime.now().isoformat()
        } 