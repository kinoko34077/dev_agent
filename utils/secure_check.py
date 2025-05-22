# /c:/Users/user/Programs/dev_agent/utils/secure_check.py

import os
import logging

# TODO: S1A2でこのルールをconfigファイルなどから読み込むようにする
# 現在はディレクトリに基づいた簡易的なルールを仮定義
# ルートパスからの相対パスで定義
ACCESS_RULES = {
    "sandbox/": ["read", "write"],
    "memory/": ["read", "write"],
    "core/": ["read"],
    "function/": ["read"], # function_loaderなどが読み込むため
    "backup/": ["read"], # バックアップ読み取り用
    # その他のディレクトリはデフォルトで読み取り専用、書き込み・削除は禁止とする
}

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
    # TODO: ワークスペースルートを取得する共通の方法を導入
    workspace_root = os.path.abspath("../") # 仮にfunction/utilsからの相対パスでワークスペースルートを想定
    abs_file_path = os.path.abspath(file_path)

    if not abs_file_path.startswith(workspace_root):
         # ワークスペース外へのアクセスは原則禁止
         logging.warning(f"Attempted access outside workspace: {file_path}")
         return False

    relative_path = os.path.relpath(abs_file_path, workspace_root)
    # パス区切り文字をスラッシュに統一 (ルール定義と合わせるため)
    relative_path = relative_path.replace(os.sep, '/')

    # 末尾のスラッシュを追加し、ディレクトリとしてマッチングしやすくする
    if os.path.isdir(abs_file_path) and not relative_path.endswith('/'):
         relative_path += '/'
    elif not os.path.exists(abs_file_path) and not relative_path.endswith('/') and not '.' in os.path.basename(relative_path):
         # 存在しないパスで、かつファイル拡張子がなく、末尾にスラッシュもない場合はディレクトリとして扱う可能性がある
         # 例: 新規作成しようとしているディレクトリ 'new_dir'
         # ここは厳密な判定が必要だが、一旦簡易的にディレクトリとして扱ってみるケースも考慮
         # ただし、基本は存在するファイル/ディレクトリへのチェックがメインとなる想定
         pass # 現時点では特に何もしない


    # 定義されたルールをチェック
    for pattern, allowed_actions in ACCESS_RULES.items():
        if relative_path.startswith(pattern):
            if action in allowed_actions:
                logging.debug(f"Permission granted for '{file_path}' (rule: {pattern}, action: {action})")
                return True
            else:
                logging.warning(f"Permission denied for '{file_path}' (rule: {pattern}, action: {action})")
                return False

    # どのルールにもマッチしない場合のデフォルトポリシー
    # デフォルトは読み取り専用、書き込み・削除は禁止
    default_allowed_actions = ["read"] # 読み取りのみ許可
    if action in default_allowed_actions:
        logging.debug(f"Permission granted (default) for '{file_path}' (action: {action})")
        return True
    else:
        logging.warning(f"Permission denied (default) for '{file_path}' (action: {action})")
        return False

if __name__ == '__main__':
    # テストコード例
    print("--- Permission Check Tests ---")

    # 仮のワークスペースルートとファイルパスを設定 (テスト用)
    # TODO: テスト時に実際のファイルを作成・削除する処理を追加
    temp_workspace_root = os.path.abspath("./") # secure_check.pyのあるディレクトリを仮のルートとする
    sandbox_file = os.path.join(temp_workspace_root, "../sandbox/functions/test_func.py")
    core_file = os.path.join(temp_workspace_root, "../core/some_module.py")
    memory_file = os.path.join(temp_workspace_root, "../memory/some_data.json")
    other_file = os.path.join(temp_workspace_root, "some_other_file.txt") # ルールにない場所

    # 存在しないファイルのパスも考慮
    non_existent_sandbox_file = os.path.join(temp_workspace_root, "../sandbox/functions/new_func.py")
    non_existent_other_file = os.path.join(temp_workspace_root, "new_dir/new_file.txt")


    print(f"Check write permission for {sandbox_file}: {check_permission(sandbox_file, 'write')}") # Expected: True
    print(f"Check read permission for {sandbox_file}: {check_permission(sandbox_file, 'read')}")   # Expected: True
    print(f"Check delete permission for {sandbox_file}: {check_permission(sandbox_file, 'delete')}") # Expected: False (現在deleteアクションはルールにないため)

    print(f"Check write permission for {core_file}: {check_permission(core_file, 'write')}")       # Expected: False
    print(f"Check read permission for {core_file}: {check_permission(core_file, 'read')}")         # Expected: True

    print(f"Check write permission for {memory_file}: {check_permission(memory_file, 'write')}")     # Expected: True
    print(f"Check read permission for {memory_file}: {check_permission(memory_file, 'read')}")       # Expected: True

    print(f"Check write permission for {other_file}: {check_permission(other_file, 'write')}")       # Expected: False (デフォルトルール)
    print(f"Check read permission for {other_file}: {check_permission(other_file, 'read')}")         # Expected: True (デフォルトルール)

    print(f"Check write permission for non-existent {non_existent_sandbox_file}: {check_permission(non_existent_sandbox_file, 'write')}") # Expected: True (sandboxルール適用)
    print(f"Check write permission for non-existent {non_existent_other_file}: {check_permission(non_existent_other_file, 'write')}") # Expected: False (デフォルトルール)

    # ワークスペース外のパス (例: OSの設定ファイルなどへのアクセスを防ぐ)
    # Windowsの場合の例 (環境に合わせて適宜変更)
    if os.name == 'nt':
        system_file = "C:/Windows/System32/drivers/etc/hosts"
        print(f"Check read permission for system file {system_file}: {check_permission(system_file, 'read')}") # Expected: False
