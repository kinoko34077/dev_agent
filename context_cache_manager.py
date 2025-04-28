# context_cache_manager.py

import os
import json
import datetime
import logging
import google.generativeai as genai

CACHE_DIR = os.path.join("memory", "cache")
CACHE_FILE = os.path.join(CACHE_DIR, "context_cache.json")
CACHE_TTL_HOURS = 2  # Geminiキャッシュ最大2時間
SAFETY_MARGIN_MINUTES = 5  # 5分前で更新する

class ContextCacheManager:
    def __init__(self, model_name: str, system_prompt: str, temperature: float):
        self.model_name = model_name
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.model = None  # キャッシュ作成専用モデル
        self.cache_name = None
        self.expire_time = None

        os.makedirs(CACHE_DIR, exist_ok=True)
        self.load_cache()

    def load_cache(self):
        if not os.path.exists(CACHE_FILE):
            logging.info("キャッシュファイルが存在しません。新規作成します。")
            self.create_cache()
            return
        
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.cache_name = data["cache_name"]
            self.expire_time = datetime.datetime.fromisoformat(data["expire_time"])
            logging.info(f"キャッシュ読み込み成功：{self.cache_name}")
        except Exception as e:
            logging.error(f"キャッシュ読み込みエラー: {str(e)}")
            self.create_cache()

    def save_cache(self):
        data = {
            "cache_name": self.cache_name,
            "expire_time": self.expire_time.isoformat()
        }
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            logging.info(f"キャッシュ保存完了：{self.cache_name}")
        except Exception as e:
            logging.error(f"キャッシュ保存エラー: {str(e)}")

    def is_cache_valid(self) -> bool:
        if not self.cache_name or not self.expire_time:
            return False

        now = datetime.datetime.utcnow()
        margin = datetime.timedelta(minutes=SAFETY_MARGIN_MINUTES)
        if now < (self.expire_time - margin):
            return True
        else:
            logging.info("キャッシュ期限切れ検出。更新が必要です。")
            return False

    def create_cache(self):
        try:
            logging.info("新規キャッシュ作成中...")

            # キャッシュ専用の一時モデルを初期化
            self.model = genai.GenerativeModel(
                model_name=self.model_name,
                system_instruction=self.system_prompt,
                generation_config={
                    "temperature": self.temperature
                }
            )

            # キャッシュ作成リクエスト
            response = self.model.generate_content(
                "キャッシュ初期化ダミー（実際の応答内容は重要ではありません）",
                request_options={
                    "caching_config": {
                        "ttl": datetime.timedelta(hours=CACHE_TTL_HOURS)
                    }
                }
            )
            cached_content = response.cached_content
            self.cache_name = cached_content.name
            self.expire_time = cached_content.expire_time
            self.save_cache()
            logging.info(f"新しいキャッシュ作成成功: {self.cache_name}")

        except Exception as e:
            logging.error(f"キャッシュ作成エラー: {str(e)}")
            raise

    def get_active_cache(self) -> str:
        if not self.is_cache_valid():
            self.create_cache()
        return self.cache_name
