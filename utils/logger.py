# utils/logger.py

import logging
from datetime import datetime

# 東京時刻(UTC+9)を使う
def get_tokyo_timestamp():
    jst = datetime.utcnow().timestamp() + 9*60*60  # 9時間足し
    return datetime.fromtimestamp(jst).strftime("%y%m%d_%H:%M")

# ログ基本設定
def setup_logger():
    log_filename = f"logs/{get_tokyo_timestamp()}_system.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_filename),
            logging.StreamHandler()
        ]
    )
