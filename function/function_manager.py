# /c:/Users/user/Programs/dev_agent/function/function_manager.py

import os
import shutil
import time
import logging
from pathlib import Path

# プロジェクトルートからの絶対パスを設定
PROJECT_ROOT = Path(__file__).parent.parent
SANDBOX_FUNCTIONS_DIR = str(PROJECT_ROOT / "sandbox" / "functions")
BACKUP_FUNCTIONS_DIR = str(PROJECT_ROOT / "backup" / "functions")

def write_to_sandbox_file(name: str, code: str) -> str:
    """
    指定された関数名とコードでsandboxに関数ファイルを書き込む。
    同名のファイルが既に存在する場合はバックアップを作成する。

    Args:
        name (str): 関数名。ファイル名として使用される (例: my_function.py)。
        code (str): 書き込むPythonコード。

    Returns:
        str: 書き込み結果を示すメッセージ。成功またはエラー情報。
    """
    # ディレクトリが存在することを確認
    os.makedirs(SANDBOX_FUNCTIONS_DIR, exist_ok=True)
    os.makedirs(BACKUP_FUNCTIONS_DIR, exist_ok=True)

    file_path = os.path.join(SANDBOX_FUNCTIONS_DIR, f"{name}.py")

    # バックアップ処理
    if os.path.exists(file_path):
        timestamp = int(time.time())
        backup_file_name = f"{name}_{timestamp}.py"
        backup_file_path = os.path.join(BACKUP_FUNCTIONS_DIR, backup_file_name)
        try:
            shutil.move(file_path, backup_file_path)
            logging.info(f"Existing file {file_path} backed up to {backup_file_path}")
        except Exception as e:
            logging.error(f"Failed to backup file {file_path} to {backup_file_path}: {e}")
            # バックアップ失敗しても処理は続行（ファイルの消失を防ぐため）

    # ファイル書き込み
    try:
        # TODO: S1A1 secure_open() が実装されたら置き換える
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(code)
        logging.info(f"Function '{name}' written to {file_path}")
        return f"関数 '{name}' を作成しました: {file_path}"
    except Exception as e:
        logging.error(f"Failed to write function '{name}' to {file_path}: {e}")
        return f"エラー: 関数 '{name}' の書き込みに失敗しました: {e}"

if __name__ == '__main__':
    # テストコード例
    dummy_code_v1 = """
def my_test_function(arg1, arg2):
    \"\"\"これはテスト関数です（v1）。\"\"\"
    print(f"Args: {arg1}, {arg2}")
    return arg1 + arg2
"""
    dummy_code_v2 = """
def my_test_function(arg1, arg2):
    \"\"\"これはテスト関数です（v2 - 変更あり）。\"\"\"
    result = arg1 * arg2
    print(f"Multiplied Args: {result}")
    return result
"""

    print("--- Test 1: 新規ファイル書き込み ---")
    result1 = write_to_sandbox_file("my_test_function", dummy_code_v1)
    print(result1)
    # ファイルが作成されたか確認
    print(f"File exists after write 1: {os.path.exists(os.path.join(SANDBOX_FUNCTIONS_DIR, 'my_test_function.py'))}")

    print("\n--- Test 2: 既存ファイル上書きとバックアップ ---")
    # 少し待ってタイムスタンプを変える
    time.sleep(1)
    result2 = write_to_sandbox_file("my_test_function", dummy_code_v2)
    print(result2)
    # バックアップファイルが作成されたか確認 (ファイル名にタイムスタンプが入るので正確な名前はわからない)
    backup_files = os.listdir(BACKUP_FUNCTIONS_DIR)
    print(f"Backup files in {BACKUP_FUNCTIONS_DIR}: {backup_files}")
    print(f"File exists after write 2: {os.path.exists(os.path.join(SANDBOX_FUNCTIONS_DIR, 'my_test_function.py'))}")


    # テスト用ファイルのクリーンアップ (注意: バックアップファイルは残ります)
    # try:
    #     os.remove(os.path.join(SANDBOX_FUNCTIONS_DIR, "my_test_function.py"))
    #     print("Cleaned up my_test_function.py")
    # except OSError as e:
    #     print(f"Error during cleanup: {e}")