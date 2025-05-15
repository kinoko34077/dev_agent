# memory/memory_loader.py

import os
import json
import logging

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
        self.system_prompt = None         # システムプロンプトの内容
        self.config_snapshot = None       # モデル設定のスナップショット
        self.summary_combined = None      # 過去要約の連結文
        self.meta = None                  # メタデータ（ターン数など）

        self.load_context()

    def load_context(self):
        """
        memory/context/内の各種ファイルを個別に読み込む。
        存在しない場合はログに警告を出し、Noneのまま保持する。
        """
        try:
            # system_prompt.txt 読み込み
            system_prompt_path = os.path.join(MEMORY_CONTEXT_DIR, "system_prompt.txt")
            if os.path.exists(system_prompt_path):
                with open(system_prompt_path, "r", encoding="utf-8") as f:
                    self.system_prompt = f.read().strip()
            else:
                logging.warning("system_prompt.txt が存在しません。")

            # config_snapshot.json 読み込み
            config_snapshot_path = os.path.join(MEMORY_CONTEXT_DIR, "config_snapshot.json")
            if os.path.exists(config_snapshot_path):
                with open(config_snapshot_path, "r", encoding="utf-8") as f:
                    self.config_snapshot = json.load(f)
            else:
                logging.warning("config_snapshot.json が存在しません。")

            # summary_combined.txt 読み込み
            summary_combined_path = os.path.join(MEMORY_CONTEXT_DIR, "summary_combined.txt")
            if os.path.exists(summary_combined_path):
                with open(summary_combined_path, "r", encoding="utf-8") as f:
                    self.summary_combined = f.read().strip()
            else:
                logging.warning("summary_combined.txt が存在しません。")

            # memory_meta.json 読み込み
            memory_meta_path = os.path.join(MEMORY_CONTEXT_DIR, "memory_meta.json")
            if os.path.exists(memory_meta_path):
                with open(memory_meta_path, "r", encoding="utf-8") as f:
                    self.meta = json.load(f)
            else:
                logging.warning("memory_meta.json が存在しません。")

            logging.info("MemoryContext 読み込み完了")

        except Exception as e:
            logging.error(f"MemoryContext 読み込みエラー: {str(e)}")
            raise

    def get_system_prompt(self) -> str:
        """
        システムプロンプトを取得（存在しない場合はデフォルト返却）

        Returns:
            str: システムプロンプト文字列
        """
        return self.system_prompt or "あなたは有能な自律エージェントです。"

    def get_config_snapshot(self) -> dict:
        """
        モデル設定のスナップショットを取得

        Returns:
            dict: モデル設定辞書（空なら空辞書）
        """
        return self.config_snapshot or {}

    def get_summary_combined(self) -> str:
        """
        現在の要約情報（combined要約）を取得

        Returns:
            str: 要約文字列
        """
        return self.summary_combined or ""

    def get_meta(self) -> dict:
        """
        メモリメタ情報（ターン数、最終要約時刻など）を取得

        Returns:
            dict: メタ情報辞書（空なら空辞書）
        """
        return self.meta or {}
