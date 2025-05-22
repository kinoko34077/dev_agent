# /c:/Users/user/Programs/dev_agent/tests/test_function_system.py

import os
import sys
import logging
from pathlib import Path
import shutil
import unittest
from unittest.mock import Mock, patch

# プロジェクトのルートディレクトリをPythonパスに追加
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

from core.executor import Executor
from core.functions_registry import (
    register, get_function_schemas, get_function_metadata,
    can_gpt_call, can_gpt_edit, REGISTERED_FUNCTIONS
)
from function.function_manager import write_to_sandbox_file
from utils.secure_check import check_permission
from utils.fileio import write_file, read_file
from tests.utils import TestConfig, MockUtils, TestData, AssertionUtils

class TestFunctionSystem(unittest.TestCase):
    """関数システムのテスト"""
    
    @classmethod
    def setUpClass(cls):
        """クラス全体の前準備"""
        # プロジェクトのルートディレクトリをPythonパスに追加
        project_root = Path(__file__).parent.parent
        sys.path.append(str(project_root))
        
        # テスト用のディレクトリパス
        cls.SANDBOX_DIR = project_root / "sandbox" / "functions"
        cls.BACKUP_DIR = project_root / "backup" / "functions"
        
        # ロギングの設定
        logging.basicConfig(level=logging.INFO)
        cls.logger = logging.getLogger(__name__)
    
    def setUp(self):
        """テストの前準備"""
        self.config = TestConfig()
        self.mock_utils = MockUtils()
        self.test_data = TestData()
        self.assertion_utils = AssertionUtils()
        
        # テスト環境のセットアップ
        self.setup_test_environment()
    
    def tearDown(self):
        """テストの後処理"""
        # テスト環境のクリーンアップ
        self.cleanup_test_environment()
    
    def setup_test_environment(self):
        """テスト環境のセットアップ"""
        # 既存のsandboxとbackupをクリーンアップ
        if self.SANDBOX_DIR.exists():
            shutil.rmtree(self.SANDBOX_DIR)
        if self.BACKUP_DIR.exists():
            shutil.rmtree(self.BACKUP_DIR)
        
        # ディレクトリを作成
        os.makedirs(self.SANDBOX_DIR, exist_ok=True)
        os.makedirs(self.BACKUP_DIR, exist_ok=True)
    
    def cleanup_test_environment(self):
        """テスト環境のクリーンアップ"""
        if self.SANDBOX_DIR.exists():
            shutil.rmtree(self.SANDBOX_DIR)
        if self.BACKUP_DIR.exists():
            shutil.rmtree(self.BACKUP_DIR)
    
    def test_function_loader(self):
        """F2B1: 関数ローダーのテスト"""
        self.logger.info("\n=== Testing Function Loader (F2B1) ===")
        
        # テスト用の関数を作成
        test_func_code = """
def test_function(x: int, y: int) -> int:
    \"\"\"テスト用の関数です。\"\"\"
    return x + y
"""
        # sandboxに関数を書き込む
        result = write_to_sandbox_file("test_function", test_func_code)
        self.logger.info(f"Write result: {result}")

        # ファイルが正しく作成されたか確認
        test_file = self.SANDBOX_DIR / "test_function.py"
        self.assertTrue(test_file.exists(), f"テストファイルが作成されていません: {test_file}")

        # Executorを初期化して関数をロード
        executor = Executor()
        loaded_functions = list(executor.available_functions.keys())
        self.logger.info(f"Loaded functions: {loaded_functions}")
        
        self.assertIn("test_function", loaded_functions, "test_functionが正しくロードされていません")

    def test_function_schemas(self):
        """F1A3: 関数スキーマ生成のテスト"""
        self.logger.info("\n=== Testing Function Schemas (F1A3) ===")
        
        executor = Executor()
        schemas = executor.get_function_schemas()
        
        self.logger.info(f"Generated {len(schemas)} schemas:")
        for schema in schemas:
            self.logger.info(f"- {schema['name']}: {schema['description']}")
            self.logger.info(f"  Parameters: {schema['parameters']}")
            
            # スキーマの検証
            self.assertIn("name", schema)
            self.assertIn("description", schema)
            self.assertIn("parameters", schema)

    def test_secure_file_operations(self):
        """S1A1: セキュアなファイル操作のテスト"""
        self.logger.info("\n=== Testing Secure File Operations (S1A1) ===")
        
        # 権限チェックのテスト
        test_paths = [
            ("sandbox/functions/test.py", "write", True),  # sandboxは書き込み可能
            ("core/test.py", "write", False),  # coreは書き込み不可
            ("memory/test.json", "write", True),  # memoryは書き込み可能
            ("backup/test.py", "write", False)  # backupは書き込み不可（デフォルトルール）
        ]
        
        for path, action, expected in test_paths:
            result = check_permission(path, action)
            self.logger.info(f"Permission check for {path} ({action}): {result}")
            self.assertEqual(result, expected, f"権限チェックが期待通りではありません: {path}")

    def test_backup_functionality(self):
        """F3B4: バックアップ機能のテスト"""
        self.logger.info("\n=== Testing Backup Functionality (F3B4) ===")
        
        # テスト用の関数を作成（バージョン1）
        func_v1 = """
def backup_test_function():
    \"\"\"バックアップテスト用関数 v1\"\"\"
    return "version 1"
"""
        # 書き込み
        write_to_sandbox_file("backup_test", func_v1)
        
        # 同じ関数を更新（バージョン2）
        func_v2 = """
def backup_test_function():
    \"\"\"バックアップテスト用関数 v2\"\"\"
    return "version 2"
"""
        write_to_sandbox_file("backup_test", func_v2)
        
        # バックアップディレクトリの内容を確認
        self.assertTrue(self.BACKUP_DIR.exists(), "バックアップディレクトリが存在しません")
        backups = list(self.BACKUP_DIR.glob("backup_test_*.py"))
        self.logger.info(f"Found {len(backups)} backup files:")
        for backup in backups:
            self.logger.info(f"- {backup.name}")
        
        self.assertGreater(len(backups), 0, "バックアップファイルが作成されていません")

    def test_function_registry_structure(self):
        """F1A2: 関数レジストリの構造化テスト"""
        self.logger.info("\n=== Testing Function Registry Structure (F1A2) ===")
        
        # テスト用の関数を登録
        @register(
            tags=["test", "core"],
            allow_gpt_call=True,
            allow_edit=True,
            description="テスト用の関数です"
        )
        def test_registry_function(x: int, y: int) -> int:
            """テスト用の関数です"""
            return x + y
        
        # 関数が正しく登録されているか確認
        self.assertIn("test_registry_function", REGISTERED_FUNCTIONS, "関数が正しく登録されていません")
        
        # メタデータの確認
        metadata = get_function_metadata("test_registry_function")
        self.assertIsNotNone(metadata, "メタデータが取得できません")
        
        self.logger.info(f"Function metadata: tags={metadata.tags}, allow_gpt_call={metadata.allow_gpt_call}, allow_edit={metadata.allow_edit}")
        
        # メタデータの検証
        self.assertEqual(metadata.tags, ["test", "core"])
        self.assertTrue(metadata.allow_gpt_call)
        self.assertTrue(metadata.allow_edit)
        
        # スキーマの確認
        schemas = get_function_schemas()
        test_schema = next((s for s in schemas if s["name"] == "test_registry_function"), None)
        self.assertIsNotNone(test_schema, "スキーマが取得できません")
        
        self.logger.info(f"Function schema: {test_schema}")
        
        # スキーマの検証
        self.assertEqual(test_schema["name"], "test_registry_function")
        self.assertIn("parameters", test_schema)
        
        # 権限チェックの確認
        self.assertTrue(can_gpt_call("test_registry_function"), "GPT呼び出し権限が正しく設定されていません")
        self.assertTrue(can_gpt_edit("test_registry_function"), "GPT編集権限が正しく設定されていません")

if __name__ == "__main__":
    unittest.main()