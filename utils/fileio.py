# utils/fileio.py

import os
import json
import logging
from typing import IO # 型ヒントのためにIOをインポート

# secure_checkモジュールをインポート
from utils.secure_check import check_permission

# ファイルパスの正規化とワークスペースルートからの相対パス取得ヘルパー
def _normalize_path(file_path: str) -> str:
    """
    ファイルパスを正規化し、ワークスペースルートからの相対パスを返す。
    """
    # TODO: ワークスペースルートを取得する共通の方法を導入 (secure_check.pyと共有)
    workspace_root = os.path.abspath("../") # 仮にutilsからの相対パスでワークスペースルートを想定
    abs_file_path = os.path.abspath(file_path)

    # ワークスペース外の場合は元のパスを返す（check_permissionで判定される）
    if not abs_file_path.startswith(workspace_root):
         return file_path

    relative_path = os.path.relpath(abs_file_path, workspace_root)
    return relative_path.replace(os.sep, '/') # パス区切り文字をスラッシュに統一

def secure_open(file_path: str, mode: str, encoding: str = 'utf-8') -> IO:
    """
    権限チェックを行った上でファイルをオープンする。

    Args:
        file_path (str): オープンするファイルのパス。
        mode (str): ファイルオープンモード ('r', 'w', 'a', 'rb', 'wb', 'ab' など)。
        encoding (str): エンコーディング。デフォルトは'utf-8'。

    Returns:
        IO: ファイルオブジェクト。

    Raises:
        PermissionError: 権限がない場合。
        FileNotFoundError: ファイルが存在しない場合（読み込みモード）。
    """
    # TODO: 厳密なアクション判定 ('r' -> 'read', 'w'/'a' -> 'write', 'x' -> 'write'/'execute'?)
    action = 'read' if 'r' in mode else ('write' if 'w' in mode or 'a' in mode or 'x' in mode else 'unknown')

    if action == 'unknown':
        logging.warning(f"不明なファイルオープンモード: {mode} for {file_path}")
        # 不明なアクションはデフォルトで不許可とするか、必要に応じて拡張
        raise PermissionError(f"Unsupported file mode: {mode}")


    # 権限チェック
    if not check_permission(_normalize_path(file_path), action):
        raise PermissionError(f"Permission denied to '{action}' file: {file_path}")

    try:
        # 権限があれば標準のopenでファイルを開く
        return open(file_path, mode, encoding=encoding)
    except FileNotFoundError:
        # check_permissionを通過してもファイルが存在しない場合はFileNotFoundErrorを発生させる
        if 'r' in mode: # 読み込みモードの場合のみFileNotFoundError
             raise
        elif 'w' in mode or 'a' in mode or 'x' in mode:
             # 書き込みモードなどでファイルが存在しない場合は新規作成なのでOK
             return open(file_path, mode, encoding=encoding)
        else:
             # その他のモードでファイルが存在しない場合
             raise
    except Exception as e:
        logging.error(f"Error opening file {file_path} with mode {mode}: {e}")
        raise


def secure_remove(file_path: str):
    """
    権限チェックを行った上でファイルを削除する。

    Args:
        file_path (str): 削除するファイルのパス。

    Raises:
        PermissionError: 削除権限がない場合。
        FileNotFoundError: ファイルが存在しない場合。
    """
    action = 'delete'

    # 権限チェック
    if not check_permission(_normalize_path(file_path), action):
        raise PermissionError(f"Permission denied to '{action}' file: {file_path}")

    try:
        os.remove(file_path)
        logging.info(f"Securely removed file: {file_path}")
    except FileNotFoundError:
        logging.warning(f"Attempted to remove non-existent file: {file_path}")
        raise # 削除対象が存在しない場合はエラーとする
    except Exception as e:
        logging.error(f"Error removing file {file_path}: {e}")
        raise


def read_file(path: str, ext: str = "txt"):
    """拡張子に応じてファイルを読み込む（txt/json）"""
    # secure_openを使用してファイルを読み込む
    if ext == "json":
        with secure_open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    elif ext == "txt":
        with secure_open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    else:
        raise ValueError(f"未対応の拡張子: .{ext}")

def write_file(path: str, content, ext: str = "txt"):
    """拡張子に応じてファイルを書き込む（txt/json）"""
    # secure_openを使用してファイルを書き込む
    if ext == "json":
        with secure_open(path, "w", encoding="utf-8") as f:
            json.dump(content, f, indent=2, ensure_ascii=False)
    elif ext == "txt":
        with secure_open(path, "w", encoding="utf-8") as f:
            f.write(content)
    else:
        raise ValueError(f"未対応の拡張子: .{ext}")
