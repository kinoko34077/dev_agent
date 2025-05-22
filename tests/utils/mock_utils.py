"""
モックユーティリティモジュール
"""

from unittest.mock import Mock, patch
from typing import Any, Dict, Optional
from .test_config import TestConfig

class MockUtils:
    """モックユーティリティクラス"""
    
    @staticmethod
    def create_mock_llm_client() -> Mock:
        """
        LLMクライアントのモックを作成
        
        Returns:
            Mock: LLMクライアントのモック
        """
        mock_client = Mock()
        mock_client.ask.return_value = "テスト応答"
        return mock_client
    
    @staticmethod
    def create_mock_dialogue() -> Mock:
        """
        内的対話システムのモックを作成
        
        Returns:
            Mock: 内的対話システムのモック
        """
        mock_dialogue = Mock()
        mock_dialogue.start_dialogue.return_value = "テスト対話開始"
        mock_dialogue.continue_dialogue.return_value = "テスト対話継続"
        mock_dialogue.end_dialogue.return_value = {
            "topic": "テストトピック",
            "duration_seconds": 1.0,
            "message_count": 2,
            "is_active": False
        }
        return mock_dialogue
    
    @staticmethod
    def create_mock_recursion_manager() -> Mock:
        """
        再帰マネージャーのモックを作成
        
        Returns:
            Mock: 再帰マネージャーのモック
        """
        mock_manager = Mock()
        mock_manager.get_state.return_value = {
            "count": 0,
            "is_active": False,
            "last_error": None,
            "dialogue_history": []
        }
        return mock_manager
    
    @staticmethod
    def patch_config_loader() -> patch:
        """
        設定ローダーのパッチを作成
        
        Returns:
            patch: 設定ローダーのパッチ
        """
        config = TestConfig()
        return patch("utils.config_loader.load_config", return_value=config.get_config())
    
    @staticmethod
    def create_mock_output_manager() -> Mock:
        """
        出力マネージャーのモックを作成
        
        Returns:
            Mock: 出力マネージャーのモック
        """
        mock_output = Mock()
        mock_output.output = Mock()
        return mock_output 