# utils/logger.py

import logging
import os
from datetime import datetime

# ----------------------------------------
# ロガー初期化ユーティリティ
# - ログファイルは logs/ に時刻付きで保存
# - JST（東京時刻, UTC+9）でタイムスタンプを記録
# - コンソール出力も併用
# ----------------------------------------

def get_tokyo_timestamp() -> str:
    """
    現在時刻を東京時刻（UTC+9）に変換し、ログ用タイムスタンプ文字列として返す

    Returns:
        str: 'YYMMDD_HH:MM'形式のタイムスタンプ
    """
    jst = datetime.utcnow().timestamp() + 9 * 60 * 60  # JST = UTC + 9h
    return datetime.fromtimestamp(jst).strftime("%y%m%d_%H%M")


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
