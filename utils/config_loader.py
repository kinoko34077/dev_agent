# utils/config_loader.py

import os
import yaml
import logging
from dotenv import load_dotenv

# 初回ロード：.envを反映
load_dotenv()

CONFIG_CACHE = None  # グローバルキャッシュ

def load_config() -> dict:
    """
    config/config.yaml を読み込む（キャッシュ付き）

    Returns:
        dict: 全体の構成設定
    """
    global CONFIG_CACHE
    if CONFIG_CACHE:
        return CONFIG_CACHE

    config_path = os.path.join(os.getcwd(), "config", "config.yaml")
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"config.yaml が見つかりません: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        CONFIG_CACHE = yaml.safe_load(f)
        return CONFIG_CACHE

def get_prompt_base() -> str:
    """
    ベースプロンプトファイルを取得して返す

    Returns:
        str: システムプロンプト文字列
    """
    config = load_config()
    path = config.get("model", {}).get("prompt_base_path", "prompts/prompt_base.md")
    if not os.path.exists(path):
        logging.warning(f"プロンプトベースが存在しません: {path}")
        return "あなたは有能なアシスタントです。"
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def get_env_key(key_name: str) -> str:
    """
    指定されたキー名で .env または OS環境変数から値を取得

    Args:
        key_name (str): 例: 'GEMINI_API_KEY'

    Returns:
        str: 対応する値
    """
    val = os.getenv(key_name)
    if not val:
        raise ValueError(f"環境変数 {key_name} が未設定です。")
    return val
