# utils/helpers.py

import os
from datetime import datetime

def get_timestamp(fmt: str = "%y%m%d_%H%M") -> str:
    """現在時刻を指定形式で取得（デフォルトは YYMMDD_HHMM）"""
    return datetime.now().strftime(fmt)

def get_tokyo_timestamp(fmt: str = "%y%m%d_%H%M") -> str:
    """東京時刻（UTC+9）ベースのタイムスタンプ"""
    jst = datetime.utcnow().timestamp() + 9 * 60 * 60
    return datetime.fromtimestamp(jst).strftime(fmt)

def safe_mkdir(path: str):
    """指定ディレクトリがなければ作成"""
    os.makedirs(path, exist_ok=True)

def abs_path(*parts) -> str:
    """ルートからの絶対パス構築（OS正規化あり）"""
    return os.path.normpath(os.path.join(os.getcwd(), *parts))
