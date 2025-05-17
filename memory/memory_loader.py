# memory/memory_loader.py

import os, logging
from utils.fileio import read_file

# ----------------------------------------
# MemoryContext: memory/context/配下に存在する
# - システムプロンプト（system_prompt.txt）
# - モデル設定スナップショット（config_snapshot.json）
# - 要約ファイル（summary_combined.txt）
# - メタデータ（memory_meta.json）
# を一括読み込み・提供するユーティリティクラス
# ----------------------------------------

# メモリコンテキストのルートディレクトリ
MEMORY_CONTEXT_DIR = os.path.join("memory", "context")

class MemoryContext:
    """
    memory/context/配下の各種記録ファイルを読み込んで保持するクラス。
    他モジュールから必要情報を取得するためのゲートウェイとして機能する。
    """
    def __init__(self):
        self.system_prompt = None
        self.config_snapshot = None
        self.summary_combined = None
        self.meta = None
        self.load_context()

    def load_context(self):
        try:
            self.system_prompt = read_file(os.path.join(MEMORY_CONTEXT_DIR, "system_prompt.txt"), "txt")
            self.config_snapshot = read_file(os.path.join(MEMORY_CONTEXT_DIR, "config_snapshot.json"), "json")
            self.summary_combined = read_file(os.path.join(MEMORY_CONTEXT_DIR, "summary_combined.txt"), "txt")
            self.meta = read_file(os.path.join(MEMORY_CONTEXT_DIR, "memory_meta.json"), "json")
        except Exception as e:
            import logging
            logging.error(f"MemoryContext 読み込みエラー: {str(e)}")
            raise

    def get_system_prompt(self) -> str:
        return self.system_prompt or "あなたは有能な自律エージェントです。"

    def get_config_snapshot(self) -> dict:
        return self.config_snapshot or {}

    def get_summary_combined(self) -> str:
        return self.summary_combined or ""

    def get_meta(self) -> dict:
        return self.meta or {}