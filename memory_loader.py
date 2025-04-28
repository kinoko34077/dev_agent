# memory_loader.py

import os
import json
import logging

MEMORY_CONTEXT_DIR = os.path.join("memory", "context")

class MemoryContext:
    def __init__(self):
        self.system_prompt = None
        self.config_snapshot = None
        self.summary_combined = None
        self.meta = None

        self.load_context()

    def load_context(self):
        try:
            # system_prompt.txt 読込
            system_prompt_path = os.path.join(MEMORY_CONTEXT_DIR, "system_prompt.txt")
            if os.path.exists(system_prompt_path):
                with open(system_prompt_path, "r", encoding="utf-8") as f:
                    self.system_prompt = f.read().strip()
            else:
                logging.warning("system_prompt.txtが存在しません。")

            # config_snapshot.json 読込
            config_snapshot_path = os.path.join(MEMORY_CONTEXT_DIR, "config_snapshot.json")
            if os.path.exists(config_snapshot_path):
                with open(config_snapshot_path, "r", encoding="utf-8") as f:
                    self.config_snapshot = json.load(f)
            else:
                logging.warning("config_snapshot.jsonが存在しません。")

            # summary_combined.txt 読込（空可）
            summary_combined_path = os.path.join(MEMORY_CONTEXT_DIR, "summary_combined.txt")
            if os.path.exists(summary_combined_path):
                with open(summary_combined_path, "r", encoding="utf-8") as f:
                    self.summary_combined = f.read().strip()
            else:
                logging.warning("summary_combined.txtが存在しません。")

            # memory_meta.json 読込
            memory_meta_path = os.path.join(MEMORY_CONTEXT_DIR, "memory_meta.json")
            if os.path.exists(memory_meta_path):
                with open(memory_meta_path, "r", encoding="utf-8") as f:
                    self.meta = json.load(f)
            else:
                logging.warning("memory_meta.jsonが存在しません。")

            logging.info("MemoryContext読み込み完了")

        except Exception as e:
            logging.error(f"MemoryContext読み込みエラー: {str(e)}")
            raise

    def get_system_prompt(self) -> str:
        return self.system_prompt or "あなたは有能な自律エージェントです。"

    def get_config_snapshot(self) -> dict:
        return self.config_snapshot or {}

    def get_summary_combined(self) -> str:
        return self.summary_combined or ""

    def get_meta(self) -> dict:
        return self.meta or {}
