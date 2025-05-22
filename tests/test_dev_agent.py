# test_dev_agent.py

# ----------------------------------------
# 内的対話システムの動作検証スクリプト
# 実行すると dummy_main が呼び出され、内的対話を開始 → 自己改善を確認
# ----------------------------------------

import time
import logging
from core.recursion_manager import RecursionManager, RecursionState
from core.internal_dialogue import InternalDialogue
import unittest
from unittest.mock import Mock, patch
from datetime import datetime
from pathlib import Path
import json
from tests.utils import TestConfig, MockUtils, TestData, AssertionUtils

def dummy_main(recurse=False):
    """
    テスト用のダミーmain関数。
    - 初回呼び出し時には内的対話を開始
    - 再帰呼び出し時には完了メッセージのみ表示
    """
    print(f"\n=== ダミー自己ターン開始 (recurse={recurse}) ===")

    if not recurse:
        # 最初のターンで意図的に内的対話を開始
        print("→ 内的対話を開始")
        dialogue = InternalDialogue()
        response = dialogue.start_dialogue("テスト用の内的対話")
        print(f"内的対話応答: {response}")
        
        # 対話の継続を試みる
        try:
            analysis = dialogue.continue_dialogue(response)
            print(f"内的対話分析: {analysis}")
        except Exception as e:
            print(f"内的対話継続エラー: {str(e)}")
    else:
        # 再帰後のターンでは処理を終了
        print("✅ 自己ターン再帰実行完了！")
        
        # 状態を確認
        state = recursion_manager.get_state()
        print("\n=== 再帰状態の確認 ===")
        print(f"実行回数: {state['count']}")
        print(f"最終エラー: {state['last_error']}")
        print(f"対話履歴: {len(state['dialogue_history'])}件")

class TestRecursionManager(unittest.TestCase):
    """再帰マネージャーのテスト"""
    
    def setUp(self):
        """テストの前準備"""
        self.config = TestConfig()
        self.mock_utils = MockUtils()
        self.test_data = TestData()
        self.assertion_utils = AssertionUtils()
        
        # テスト環境のセットアップ
        self.config.setup_test_environment()
        
        # モックの設定
        self.mock_main = Mock()
        self.mock_dialogue = self.mock_utils.create_mock_dialogue()
        self.config_patcher = self.mock_utils.patch_config_loader()
        self.dialogue_patcher = patch("core.recursion_manager.InternalDialogue", return_value=self.mock_dialogue)
        
        # パッチの開始
        self.config_patcher.start()
        self.dialogue_patcher.start()
        
        # 再帰マネージャーの初期化
        self.recursion_manager = RecursionManager(self.mock_main)

    def tearDown(self):
        """テストの後処理"""
        # パッチの解除
        self.config_patcher.stop()
        self.dialogue_patcher.stop()
        
        # テスト用の出力ファイルを削除
        for file in self.config.output_dir.glob("dialogue_*.json"):
            file.unlink()

    def test_initialization(self):
        """初期化のテスト"""
        self.assertion_utils.assert_recursion_state(self.recursion_manager.state.__dict__)

    def test_handle_recursion_normal(self):
        """正常系の再帰処理テスト"""
        self.mock_main.return_value = "テスト結果"
        
        result = self.recursion_manager.handle_recursion()
        
        # 結果の検証
        self.assertIn(result, [None, "テスト結果"])
        self.assertEqual(self.recursion_manager.state.count, 1)
        
        # モックの呼び出し確認
        self.mock_dialogue.start_dialogue.assert_called_once()
        self.mock_dialogue.continue_dialogue.assert_called_once()
        self.mock_dialogue.end_dialogue.assert_called_once()

    def test_handle_recursion_with_error(self):
        """エラー発生時の再帰処理テスト"""
        self.mock_main.side_effect = Exception("テストエラー")
        
        with self.assertRaises(Exception):
            self.recursion_manager.handle_recursion()
        
        # 状態の検証
        self.assertEqual(self.recursion_manager.state.count, 0)
        self.assertFalse(self.recursion_manager.state.is_active)
        self.assertIsNotNone(self.recursion_manager.state.last_error)
        self.mock_dialogue.end_dialogue.assert_called_once()

    def test_should_retry(self):
        """再試行判定のテスト"""
        # 再試行可能なエラー
        self.recursion_manager.state.last_error = "timeout error"
        self.assertTrue(self.recursion_manager.should_retry())
        
        # 再試行不可能なエラー
        self.recursion_manager.state.last_error = "400 Bad Request"
        self.assertFalse(self.recursion_manager.should_retry())
        
        # エラーなし
        self.recursion_manager.state.last_error = None
        self.assertFalse(self.recursion_manager.should_retry())

    def test_get_state(self):
        """状態取得のテスト"""
        state = self.recursion_manager.get_state()
        self.assertion_utils.assert_recursion_state(state)

    def test_reset_state(self):
        """状態リセットのテスト"""
        # 状態の変更
        self.recursion_manager.state.count = 5
        self.recursion_manager.state.is_active = True
        self.recursion_manager.state.last_error = "テストエラー"
        
        # リセットの実行
        self.recursion_manager.reset_state()
        
        # 状態の検証
        self.assertion_utils.assert_recursion_state(self.recursion_manager.state.__dict__)
        self.assertEqual(self.recursion_manager.state.count, 0)
        self.assertFalse(self.recursion_manager.state.is_active)
        self.assertIsNone(self.recursion_manager.state.last_error)

    def test_disabled_recursion(self):
        """無効化時のテスト"""
        self.config.update_config("recursion", {"enabled": False})
        recursion_manager = RecursionManager(self.mock_main)
        
        result = recursion_manager.handle_recursion()
        
        # 結果の検証
        self.assertIsNone(result)
        self.mock_main.assert_called_once()
        self.mock_dialogue.start_dialogue.assert_not_called()
        self.mock_dialogue.continue_dialogue.assert_not_called()
        self.mock_dialogue.end_dialogue.assert_not_called()

if __name__ == "__main__":
    # ロギングの設定
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    # 再帰マネージャにdummy_mainを渡して起動
    recursion_manager = RecursionManager()
    
    try:
        # 初回呼び出し
        dummy_main()
        
        # 再帰処理を確認
        recursion_manager.handle_recursion()
        
        # 最終状態の確認
        final_state = recursion_manager.get_state()
        print("\n=== 最終状態 ===")
        print(f"実行回数: {final_state['count']}")
        print(f"最終エラー: {final_state['last_error']}")
        print(f"対話履歴: {len(final_state['dialogue_history'])}件")
        
        # 対話履歴の詳細表示
        print("\n=== 対話履歴の詳細 ===")
        for entry in final_state['dialogue_history']:
            print(f"\nタイプ: {entry['type']}")
            print(f"タイムスタンプ: {entry['timestamp']}")
            if 'topic' in entry:
                print(f"トピック: {entry['topic']}")
            if 'response' in entry:
                print(f"応答: {entry['response'][:100]}...")
            if 'analysis' in entry:
                print(f"分析: {entry['analysis'][:100]}...")
            if 'summary' in entry:
                print(f"サマリー: {entry['summary'][:100]}...")
                
    except Exception as e:
        logging.error(f"テスト実行中にエラーが発生: {str(e)}")
    finally:
        # 状態をリセット
        recursion_manager.reset_state()

    unittest.main()
