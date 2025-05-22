# /c:/Users/user/Programs/dev_agent/utils/secure_check.py

import os
import logging
import yaml
import ast
from pathlib import Path
from typing import Any, Dict, List

# 設定ファイル読み込みの共通処理
def load_config() -> Dict[str, Any]:
    """
    config.yamlから設定を読み込む
    Returns:
        dict: 設定の辞書
    """
    try:
        config_path = Path(__file__).parent.parent / 'config' / 'config.yaml'
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except Exception as e:
        logging.error(f"Failed to load config: {e}")
        return {}

# アクセスルールの読み込み
def load_access_rules() -> dict:
    """
    アクセスルールをconfig/access.yamlから読み込む
    Returns:
        dict: アクセスルールの辞書
    """
    try:
        config_path = Path(__file__).parent.parent / 'config' / 'access.yaml'
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        return config.get('access_rules', {})
    except Exception as e:
        logging.error(f"Failed to load access rules: {e}")
        # エラー時は最小限のデフォルトルールを返す
        return {
            "sandbox/": ["read", "write"],
            "memory/": ["read", "write"],
            "core/": ["read"],
        }

# セーフモードの読み込み
def load_safe_mode() -> str:
    """
    config.yamlからsafe_modeを取得
    Returns: 'strict'|'confirm'|'permissive'
    """
    config = load_config()
    mode = config.get('execution', {}).get('safe_mode', 'strict')
    if isinstance(mode, bool):
        return 'strict' if mode else 'permissive'
    return str(mode).lower()

# 禁止操作の読み込み
def load_forbidden_operations() -> List[str]:
    """
    config.yamlから禁止操作のリストを取得
    Returns:
        list: 禁止操作のリスト
    """
    config = load_config()
    return config.get('execution', {}).get('forbidden_operations', [])

# 設定を読み込む
ACCESS_RULES = load_access_rules()
SAFE_MODE = load_safe_mode()
FORBIDDEN_OPERATIONS = load_forbidden_operations()

# セキュリティ関連のエラー
class SecurityError(Exception):
    """セキュリティ関連のエラー"""
    pass

# コード安全性チェック
def is_code_safe(code: str) -> bool:
    """
    コードが安全かどうかをチェックする
    Args:
        code (str): チェック対象のコード文字列
    Returns:
        bool: 安全な場合はTrue、そうでなければFalse
    Raises:
        SecurityError: コードが安全でない場合
    """
    # 構文チェック
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise SecurityError(f"Invalid syntax: {e}")

    # 禁止操作のチェック
    for node in ast.walk(tree):
        # 関数呼び出しのチェック
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in FORBIDDEN_OPERATIONS:
                    raise SecurityError(f"Forbidden operation: {node.func.id}")
            elif isinstance(node.func, ast.Attribute):
                if f"{node.func.value.id}.{node.func.attr}" in FORBIDDEN_OPERATIONS:
                    raise SecurityError(f"Forbidden operation: {node.func.value.id}.{node.func.attr}")

    return True

# アクセス権限チェック
def check_permission(file_path: str, action: str) -> bool:
    """
    指定されたファイルパスに対して、要求されたアクションが許可されているかチェックする。
    Args:
        file_path (str): チェック対象のファイルパス (ワークスペースルートからの相対パス推奨)。
        action (str): 要求されるアクション ('read', 'write', 'delete' など)。
    Returns:
        bool: 許可されていればTrue、そうでなければFalse。
    """
    logging.debug(f"Permission check: path='{file_path}', action='{action}'")

    # パスを正規化してワークスペースルートからの相対パスに変換
    workspace_root = Path(__file__).parent.parent.absolute()
    abs_file_path = Path(file_path).absolute()

    # ワークスペース外へのアクセスは原則禁止
    if not str(abs_file_path).startswith(str(workspace_root)):
        logging.warning(f"Attempted access outside workspace: {file_path}")
        return False

    # 相対パスに変換
    relative_path = str(abs_file_path.relative_to(workspace_root))
    relative_path = relative_path.replace(os.sep, '/')

    # ディレクトリの場合は末尾にスラッシュを追加
    if abs_file_path.is_dir() and not relative_path.endswith('/'):
        relative_path += '/'

    # 定義されたルールをチェック
    for pattern, rule in ACCESS_RULES.items():
        if relative_path.startswith(pattern):
            if rule.get(action, False):
                logging.debug(f"Permission granted for '{file_path}' (rule: {pattern}, action: {action})")
                return True
            else:
                logging.warning(f"Permission denied for '{file_path}' (rule: {pattern}, action: {action})")
                return False

    # デフォルトルールを適用
    default_rule = ACCESS_RULES.get('default_rule', {'read': False, 'write': False})
    if default_rule.get(action, False):
        logging.debug(f"Permission granted (default) for '{file_path}' (action: {action})")
        return True
    else:
        # safe_modeによる分岐
        if SAFE_MODE == 'strict':
            logging.warning(f"Permission denied (strict mode, default) for '{file_path}' (action: {action})")
            return False
        elif SAFE_MODE == 'confirm':
            try:
                ans = input(f"[SECURITY] '{file_path}' で '{action}' を実行しますか？ (y/n): ").strip().lower()
                if ans == 'y':
                    logging.info(f"Permission granted by user confirmation for '{file_path}' (action: {action})")
                    return True
                else:
                    logging.warning(f"Permission denied by user confirmation for '{file_path}' (action: {action})")
                    return False
            except Exception as e:
                logging.error(f"Permission confirm failed: {e}")
                return False
        elif SAFE_MODE == 'permissive':
            logging.info(f"Permission granted (permissive mode, default) for '{file_path}' (action: {action})")
            return True
        else:
            logging.warning(f"Permission denied (unknown safe_mode, default) for '{file_path}' (action: {action})")
            return False

# テストコード
if __name__ == '__main__':
    # テストコード例
    print("--- Permission Check Tests ---")

    # ワークスペースルートを取得
    workspace_root = Path(__file__).parent.parent.absolute()
    
    # テスト用のパスを設定
    test_paths = {
        'sandbox': workspace_root / 'sandbox' / 'functions' / 'test_func.py',
        'core': workspace_root / 'core' / 'some_module.py',
        'memory': workspace_root / 'memory' / 'some_data.json',
        'other': workspace_root / 'some_other_file.txt'
    }

    # 各パスに対する権限チェック
    for name, path in test_paths.items():
        print(f"\nTesting {name} path: {path}")
        for action in ['read', 'write', 'delete']:
            result = check_permission(str(path), action)
            print(f"  {action}: {result}")

    # 存在しないパスのテスト
    non_existent_paths = {
        'sandbox_new': workspace_root / 'sandbox' / 'functions' / 'new_func.py',
        'other_new': workspace_root / 'new_dir' / 'new_file.txt'
    }

    print("\nTesting non-existent paths:")
    for name, path in non_existent_paths.items():
        print(f"\nTesting {name} path: {path}")
        for action in ['read', 'write', 'delete']:
            result = check_permission(str(path), action)
            print(f"  {action}: {result}")

    # ワークスペース外のパスのテスト
    if os.name == 'nt':
        system_file = "C:/Windows/System32/drivers/etc/hosts"
        print(f"\nTesting system file: {system_file}")
        for action in ['read', 'write', 'delete']:
            result = check_permission(system_file, action)
            print(f"  {action}: {result}")

    # コード安全性チェックのテスト
    print("\n--- Code Safety Tests ---")
    test_codes = [
        ("Safe code", "print('Hello, World!')"),
        ("Dangerous code - os.system", "os.system('rm -rf /')"),
        ("Dangerous code - eval", "eval('__import__(\"os\").system(\"rm -rf /\")')"),
    ]

    for name, code in test_codes:
        print(f"\nTesting {name}:")
        try:
            is_safe = is_code_safe(code)
            print(f"  Result: {'Safe' if is_safe else 'Dangerous'}")
        except SecurityError as e:
            print(f"  Error: {e}")
