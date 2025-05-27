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
            return yaml.safe_load(f)
    except Exception as e:
        logging.error(f"Failed to load access rules: {e}")
        # エラー時は最小限のデフォルトルールを返す
        return {
            "access_rules": {
                "sandbox/": {"read": True, "write": True},
                "memory/": {"read": True, "write": True},
                "core/": {"read": True, "write": False}
            }
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
def check_permission(path: str, operation: str) -> bool:
    """
    指定されたパスと操作に対する権限をチェックします。

    Args:
        path: チェック対象のパス
        operation: 操作タイプ（'read' または 'write'）

    Returns:
        bool: 権限がある場合はTrue、ない場合はFalse
    """
    try:
        # パスを正規化
        path_obj = Path(path)
        normalized_path = str(path_obj).replace('\\', '/')
        
        # ディレクトリの場合は末尾に/を追加
        if path_obj.is_dir() or path.endswith('/'):
            normalized_path = normalized_path.rstrip('/') + '/'
            
        # ワークスペースルートからの相対パスに変換
        workspace_root = Path(__file__).parent.parent
        try:
            relative_path = str(path_obj.relative_to(workspace_root)).replace('\\', '/')
            if path_obj.is_dir() or path.endswith('/'):
                relative_path = relative_path.rstrip('/') + '/'
        except ValueError:
            # ワークスペース外のパスの場合は絶対パスを使用
            relative_path = normalized_path
            
        logging.debug(f"Checking permission for path: {relative_path}, operation: {operation}")
        logging.debug(f"Workspace root: {workspace_root}")
        logging.debug(f"Original path: {path}")
        logging.debug(f"Normalized path: {normalized_path}")
        
        # 設定を読み込み
        config = load_config()
        access_rules = load_access_rules()
        
        # safe_modeの設定を取得
        safe_mode = config.get('execution', {}).get('safe_mode', 'strict')
        logging.debug(f"Safe mode: {safe_mode}")
        
        # パスがどのルールにマッチするか確認
        matched_rule = None
        for rule_path, rule in access_rules.get('access_rules', {}).items():
            if relative_path.startswith(rule_path):
                matched_rule = rule
                logging.debug(f"Matched rule: {rule_path} -> {rule}")
                break
        
        # ルールが見つからない場合はデフォルトルールを使用
        if matched_rule is None:
            logging.debug(f"No matching rule found for {relative_path}")
            # safe_modeに基づいてデフォルトルールを設定
            if safe_mode == 'permissive':
                return True
            elif safe_mode == 'confirm':
                # ユーザーに確認
                response = input(f"この操作を許可しますか？ ({operation} {relative_path}) [y/N]: ")
                return response.lower() == 'y'
            else:  # strict
                logging.warning(f"Permission denied ({safe_mode} mode, default) for '{relative_path}' (action: {operation})")
                return False

        # マッチしたルールの権限をチェック
        has_permission = matched_rule.get(operation, False)
        logging.debug(f"Permission check result: {has_permission}")
        return has_permission
        
    except Exception as e:
        logging.error(f"権限チェック中にエラーが発生: {str(e)}")
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
