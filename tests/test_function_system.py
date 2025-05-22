# /c:/Users/user/Programs/dev_agent/tests/test_function_system.py

import os
import sys
import logging
from pathlib import Path
import shutil

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

# ロギングの設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# テスト用のディレクトリパス
SANDBOX_DIR = project_root / "sandbox" / "functions"
BACKUP_DIR = project_root / "backup" / "functions"

def setup_test_environment():
    """テスト環境のセットアップ"""
    # 既存のsandboxとbackupをクリーンアップ
    if SANDBOX_DIR.exists():
        shutil.rmtree(SANDBOX_DIR)
    if BACKUP_DIR.exists():
        shutil.rmtree(BACKUP_DIR)
    
    # ディレクトリを作成
    os.makedirs(SANDBOX_DIR, exist_ok=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)

def test_function_loader():
    """F2B1: 関数ローダーのテスト"""
    logger.info("\n=== Testing Function Loader (F2B1) ===")
    
    # テスト用の関数を作成
    test_func_code = """
def test_function(x: int, y: int) -> int:
    \"\"\"テスト用の関数です。\"\"\"
    return x + y
"""
    # sandboxに関数を書き込む
    result = write_to_sandbox_file("test_function", test_func_code)
    logger.info(f"Write result: {result}")

    # ファイルが正しく作成されたか確認
    test_file = SANDBOX_DIR / "test_function.py"
    if not test_file.exists():
        raise AssertionError(f"テストファイルが作成されていません: {test_file}")

    # Executorを初期化して関数をロード
    executor = Executor()
    loaded_functions = list(executor.available_functions.keys())
    logger.info(f"Loaded functions: {loaded_functions}")
    
    if "test_function" not in loaded_functions:
        raise AssertionError("test_functionが正しくロードされていません")

def test_function_schemas():
    """F1A3: 関数スキーマ生成のテスト"""
    logger.info("\n=== Testing Function Schemas (F1A3) ===")
    
    executor = Executor()
    schemas = executor.get_function_schemas()
    
    logger.info(f"Generated {len(schemas)} schemas:")
    for schema in schemas:
        logger.info(f"- {schema['name']}: {schema['description']}")
        logger.info(f"  Parameters: {schema['parameters']}")

def test_secure_file_operations():
    """S1A1: セキュアなファイル操作のテスト"""
    logger.info("\n=== Testing Secure File Operations (S1A1) ===")
    
    # 権限チェックのテスト
    test_paths = [
        ("sandbox/functions/test.py", "write"),
        ("core/test.py", "write"),
        ("memory/test.json", "write"),
        ("backup/test.py", "write")
    ]
    
    for path, action in test_paths:
        result = check_permission(path, action)
        logger.info(f"Permission check for {path} ({action}): {result}")

def test_backup_functionality():
    """F3B4: バックアップ機能のテスト"""
    logger.info("\n=== Testing Backup Functionality (F3B4) ===")
    
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
    if BACKUP_DIR.exists():
        backups = list(BACKUP_DIR.glob("backup_test_*.py"))
        logger.info(f"Found {len(backups)} backup files:")
        for backup in backups:
            logger.info(f"- {backup.name}")
        
        if len(backups) == 0:
            raise AssertionError("バックアップファイルが作成されていません")

def test_function_registry_structure():
    """F1A2: 関数レジストリの構造化テスト"""
    logger.info("\n=== Testing Function Registry Structure (F1A2) ===")
    
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
    if "test_registry_function" not in REGISTERED_FUNCTIONS:
        raise AssertionError("関数が正しく登録されていません")
    
    # メタデータの確認
    metadata = get_function_metadata("test_registry_function")
    if not metadata:
        raise AssertionError("メタデータが取得できません")
    
    logger.info(f"Function metadata: tags={metadata.tags}, allow_gpt_call={metadata.allow_gpt_call}, allow_edit={metadata.allow_edit}")
    
    # スキーマの確認
    schemas = get_function_schemas()
    test_schema = next((s for s in schemas if s["name"] == "test_registry_function"), None)
    if not test_schema:
        raise AssertionError("スキーマが取得できません")
    
    logger.info(f"Function schema: {test_schema}")
    
    # 権限チェックの確認
    if not can_gpt_call("test_registry_function"):
        raise AssertionError("GPT呼び出し権限が正しく設定されていません")
    
    if not can_gpt_edit("test_registry_function"):
        raise AssertionError("GPT編集権限が正しく設定されていません")

def main():
    """全機能のテストを実行"""
    try:
        setup_test_environment()
        test_function_loader()
        test_function_schemas()
        test_secure_file_operations()
        test_backup_functionality()
        test_function_registry_structure()
        
        logger.info("\n=== All tests completed successfully ===")
    except Exception as e:
        logger.error(f"Test failed: {str(e)}", exc_info=True)
        sys.exit(1)
    finally:
        # テスト終了後のクリーンアップ
        if SANDBOX_DIR.exists():
            shutil.rmtree(SANDBOX_DIR)
        if BACKUP_DIR.exists():
            shutil.rmtree(BACKUP_DIR)

if __name__ == "__main__":
    main()