# tests/test_internal_dialogue.py

import unittest
from unittest.mock import Mock, patch
from datetime import datetime
import os
import json
from pathlib import Path

from core.internal_dialogue import InternalDialogue, InternalDialogueError, ConfigError, DialogueError
from utils.output_manager import OutputType
from tests.utils import TestConfig, MockUtils, TestData, AssertionUtils

class TestInternalDialogue(unittest.TestCase):
    """内的対話システムのテスト"""
    
    def setUp(self):
        """テストの前準備"""
        self.config = TestConfig()
        self.mock_utils = MockUtils()
        self.test_data = TestData()
        self.assertion_utils = AssertionUtils()
        
        # テスト環境のセットアップ
        self.config.setup_test_environment()
        
        # モックの設定
        self.mock_client = self.mock_utils.create_mock_llm_client()
        self.config_patcher = self.mock_utils.patch_config_loader()
        self.client_patcher = patch("core.internal_dialogue.LLMClient", return_value=self.mock_client)
        
        # パッチの開始
        self.config_patcher.start()
        self.client_patcher.start()

    def tearDown(self):
        """テストの後処理"""
        # パッチの解除
        self.config_patcher.stop()
        self.client_patcher.stop()
        
        # テスト用の出力ファイルを削除
        for file in self.config.output_dir.glob("dialogue_*.json"):
            file.unlink()

    def test_initialization(self):
        """初期化のテスト"""
        dialogue = InternalDialogue()
        self.assertTrue(dialogue.is_active)
        self.assertEqual(dialogue.max_history, self.config.get_config("internal_dialogue")["max_history"])
        self.assertEqual(len(dialogue.dialogue_history), 0)

    def test_start_dialogue(self):
        """対話開始のテスト"""
        dialogue = InternalDialogue()
        response = dialogue.start_dialogue("テストトピック")
        
        # 応答の検証
        self.assertEqual(response, "テスト応答")
        self.assertTrue(dialogue.is_active)
        
        # 対話履歴の検証
        self.assertion_utils.assert_dialogue_history(dialogue.dialogue_history, 1)
        
        # 出力ファイルの検証
        self.assertion_utils.assert_output_file(self.config.output_dir, 1)

    def test_continue_dialogue(self):
        """対話継続のテスト"""
        dialogue = InternalDialogue()
        dialogue.start_dialogue("テストトピック")
        analysis = dialogue.continue_dialogue("テスト応答")
        
        # 分析結果の検証
        self.assertEqual(analysis, "テスト応答")
        
        # 対話履歴の検証
        self.assertion_utils.assert_dialogue_history(dialogue.dialogue_history, 2)
        
        # 出力ファイルの検証
        self.assertion_utils.assert_output_file(self.config.output_dir, 1)

    def test_end_dialogue(self):
        """対話終了のテスト"""
        dialogue = InternalDialogue()
        dialogue.start_dialogue("テストトピック")
        summary = dialogue.end_dialogue()
        
        # サマリーの検証
        self.assertion_utils.assert_dialogue_summary(summary)
        self.assertFalse(dialogue.is_active)
        
        # 出力ファイルの検証
        self.assertion_utils.assert_output_file(self.config.output_dir, 1)

    def test_error_handling(self):
        """エラーハンドリングのテスト"""
        # 無効化された設定でのテスト
        self.config.update_config("internal_dialogue", {"enabled": False})
        with self.assertRaises(ConfigError):
            InternalDialogue()
        
        # 不正な設定ファイルでのテスト
        with patch("core.internal_dialogue.load_config", side_effect=FileNotFoundError):
            with self.assertRaises(ConfigError):
                InternalDialogue()

    def test_history_management(self):
        """履歴管理のテスト"""
        dialogue = InternalDialogue()
        max_history = self.config.get_config("internal_dialogue")["max_history"]
        
        # 履歴上限を超える対話を生成
        for i in range(max_history + 1):
            dialogue.start_dialogue(f"テストトピック{i}")
            dialogue.continue_dialogue(f"テスト応答{i}")
        
        # 履歴数の検証
        self.assertLessEqual(len(dialogue.dialogue_history), max_history * 2)
        
        # 出力ファイルの検証
        self.assertion_utils.assert_output_file(self.config.output_dir, 1)

    def test_state_management(self):
        """状態管理のテスト"""
        dialogue = InternalDialogue()
        self.assertFalse(dialogue.is_active)
        
        dialogue.start_dialogue("テストトピック")
        self.assertTrue(dialogue.is_active)
        
        dialogue.end_dialogue()
        self.assertFalse(dialogue.is_active)

if __name__ == '__main__':
    unittest.main() 