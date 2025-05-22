# tests/test_internal_dialogue.py

import unittest
import os
import yaml
from unittest.mock import patch, MagicMock
from core.internal_dialogue import InternalDialogue, ConfigError, DialogueError
from core.functions_registry import start_internal_dialogue, continue_internal_dialogue

class TestInternalDialogue(unittest.TestCase):
    def setUp(self):
        # テスト用の設定ファイルパスを設定し、InternalDialogueの初期化をモック
        with patch('core.internal_dialogue.InternalDialogue.__init__') as mock_init:
            mock_init.return_value = None
            self.dialogue = InternalDialogue()
            self.dialogue.config = {
                'recursion': {
                    'internal_dialogue': {
                        'enabled': True,
                        'max_history': 3,
                        'roles': {
                            'internal': 'internal',
                            'agent': 'agent'
                        }
                    }
                }
            }
            self.dialogue.internal_client = MagicMock()
            self.dialogue.dialogue_history = []
            self.dialogue.max_history = 3

    def tearDown(self):
        """テストの後処理"""
        pass

    def test_start_dialogue(self):
        """内的対話開始のテスト"""
        self.dialogue.internal_client.ask.return_value = "テスト応答"
        response = self.dialogue.start_dialogue("テストトピック")
        self.assertIsNotNone(response)
        self.assertEqual(len(self.dialogue.dialogue_history), 2)

    def test_continue_dialogue(self):
        """内的対話継続のテスト"""
        self.dialogue.internal_client.ask.return_value = "分析結果"
        response = self.dialogue.continue_dialogue("エージェントの応答")
        self.assertIsNotNone(response)
        self.assertEqual(len(self.dialogue.dialogue_history), 2)

    def test_dialogue_summary(self):
        """対話要約のテスト"""
        self.dialogue.dialogue_history = [
            {"role": "system", "content": "開始"},
            {"role": "internal", "content": "応答1"},
            {"role": "agent", "content": "応答2"},
            {"role": "internal", "content": "応答3"}
        ]
        self.dialogue.internal_client.ask.return_value = "要約結果"
        summary = self.dialogue.get_dialogue_summary()
        self.assertIsNotNone(summary)

    def test_history_limit(self):
        """履歴制限のテスト"""
        for i in range(self.dialogue.max_history + 1):
            self.dialogue.dialogue_history.append({"role": "test", "content": f"test{i}"})
        self.assertLessEqual(len(self.dialogue.dialogue_history), self.dialogue.max_history * 2)

    def test_missing_config(self):
        """設定が不足している場合のテスト"""
        with patch('core.internal_dialogue.InternalDialogue.__init__') as mock_init:
            mock_init.return_value = None
            dialogue = InternalDialogue()
            dialogue.config = {'recursion': {}}
            dialogue.internal_client = MagicMock()
            dialogue.dialogue_history = []
            # 設定が不足している状態でstart_dialogueを呼び出し、DialogueErrorが発生することを確認
            with self.assertRaises(DialogueError):
                dialogue.start_dialogue("テストトピック")

    def test_config_loading_error(self):
        """設定ファイル読み込みエラーのテスト"""
        with patch('core.internal_dialogue.InternalDialogue.__init__') as mock_init:
            mock_init.return_value = None
            dialogue = InternalDialogue()
            dialogue.config = None
            dialogue.internal_client = MagicMock()
            dialogue.dialogue_history = []
            # 設定がNoneの状態でstart_dialogueを呼び出し、DialogueErrorが発生することを確認
            with self.assertRaises(DialogueError):
                dialogue.start_dialogue("テストトピック")

    def test_registered_functions(self):
        """登録された関数のテスト"""
        from core.functions_registry import REGISTERED_FUNCTIONS
        self.assertIn('start_internal_dialogue', REGISTERED_FUNCTIONS)
        self.assertIn('continue_internal_dialogue', REGISTERED_FUNCTIONS)
        with patch('core.internal_dialogue.InternalDialogue') as mock_dialogue:
            mock_instance = mock_dialogue.return_value
            mock_instance.start_dialogue.return_value = "テスト応答"
            mock_instance.continue_dialogue.return_value = "分析結果"
            result = REGISTERED_FUNCTIONS['start_internal_dialogue'].callable("テストトピック")
            self.assertEqual(result["response"], "テスト応答")
            result = REGISTERED_FUNCTIONS['continue_internal_dialogue'].callable("エージェントの応答")
            self.assertEqual(result["analysis"], "分析結果")

if __name__ == '__main__':
    unittest.main() 