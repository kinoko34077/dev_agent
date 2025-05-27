# utils/helpers.py

import logging
import os
from datetime import datetime
import yaml

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

def setup_logger(mode="timestamp", force=False):
    """
    ロガーの設定を行う
    
    Args:
        mode: ログファイルの命名モード（"timestamp" または "system"）
        force: 既存のロガーを強制的に再設定するかどうか
    """
    if not force and logging.getLogger().handlers:
        return

    # ログディレクトリの作成
    os.makedirs("logs", exist_ok=True)

    # ルートロガーの設定
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # 既存のハンドラをクリア
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # フォーマッタの設定
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # タイムスタンプ付きのログファイル
    if mode == "timestamp":
        timestamp = datetime.now().strftime("%y%m%d_%H%M")
        log_file = os.path.join("logs", f"{timestamp}_system.log")
    else:
        log_file = os.path.join("logs", "system.log")

    # ファイルハンドラの設定
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # コンソールハンドラの設定
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    logging.info(f"ロガー設定完了: {log_file}")

def load_config(path="config/config.yaml"):
    """YAML形式の設定ファイルを読み込んで辞書として返す"""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)