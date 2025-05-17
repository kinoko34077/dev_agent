# utils/helpers.py

import logging
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

def setup_logger(mode="static", force=False):
    """
    ログ出力設定（重複初期化防止付き + 強制再初期化オプション）

    Args:
        mode (str): 'static' or 'timestamp'
        force (bool): Trueにすると既存ハンドラを全て削除して再構成する
    """
    logger = logging.getLogger()
    if logger.hasHandlers():
        if not force:
            return
        # 既存のハンドラを削除（上書き再設定）
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)

    logs_dir = os.path.join(os.getcwd(), "logs")
    os.makedirs(logs_dir, exist_ok=True)

    if mode == "timestamp":
        log_filename = f"{get_tokyo_timestamp()}_system.log"
    else:
        log_filename = "system.log"

    log_path = os.path.join(logs_dir, log_filename)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler()
        ]
    )