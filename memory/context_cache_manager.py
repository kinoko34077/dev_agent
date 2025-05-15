# context_cache_manager.py

import os
import json
import datetime
import logging
import google.generativeai as genai

# ----------------------------------------
# Google Geminiのキャッシュ管理ユーティリティ
# - キャッシュのTTL（有効期限）は2時間
# - memory/cache/context_cache.json にキャッシュ情報を保存
# - キャッシュ期限が切れていれば自動更新
# ----------------------------------------

# キャッシュ保存ディレクトリとファイルパス
CACHE_DIR = os.path.join("memory", "cache")
CACHE_FILE = os.path.join(CACHE_DIR, "context_cache.json")

# キャッシュの有効期限と安全マージン
CACHE_TTL_HOURS = 2               # 有効期限（時間単位）
SAFETY_MARGIN_MINUTES = 5         # 安全マージン（期限の5分前に更新する）

class ContextCacheManager:
    """
    Google Geminiのキャッシュを管理するクラス。
    一定時間キャッシュが有効な状態で利用可能かを判定し、
    無効であれば再生成を行う。キャッシュ名と有効期限はローカルJSONに保存。
    """

    def __init__(self, model_name: str, system_prompt: str, temperature: float):
        """
        コンストラクタ。

        Args:
            model_name (str): 使用するGeminiモデルの名称
            system_prompt (str): 使用するシステムプロンプト（context命令）
            temperature (float): 出力の創造性（温度）パラメータ
        """
        self.model_name = model_name
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.model = None               # 一時的に使用するモデルインスタンス
        self.cache_name = None         # キャッシュ識別子（Gemini側の内部名）
        self.expire_time = None        # キャッシュの有効期限（datetime）

        # キャッシュ保存ディレクトリを作成
        os.makedirs(CACHE_DIR, exist_ok=True)

        # キャッシュを読み込む（存在しない／無効な場合はcreate_cacheへ移行）
        self.load_cache()

    def load_cache(self):
        """
        ローカルJSONからキャッシュ情報を読み込む。
        ファイルが存在しない、またはパースに失敗した場合は新規作成を行う。
        """
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
        """
        現在のキャッシュ状態（名前と期限）をファイルに保存。
        """
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
        """
        現在のキャッシュがまだ有効かどうかを判定。

        Returns:
            bool: Trueなら有効、Falseなら期限切れまたは不正
        """
        if not self.cache_name or not self.expire_time:
            return False

        now = datetime.datetime.utcnow()
        margin = datetime.timedelta(minutes=SAFETY_MARGIN_MINUTES)

        # 現在時刻が有効期限マイナスマージンより前なら有効
        if now < (self.expire_time - margin):
            return True
        else:
            logging.info("キャッシュ期限切れ検出。更新が必要です。")
            return False

    def create_cache(self):
        """
        新しいキャッシュをGemini APIを通して作成し、
        キャッシュ名と期限を更新する。
        """
        try:
            logging.info("新規キャッシュ作成中...")

            # モデルインスタンスを初期化（キャッシュ作成専用）
            self.model = genai.GenerativeModel(
                model_name=self.model_name,
                system_instruction=self.system_prompt,
                generation_config={
                    "temperature": self.temperature
                }
            )

            # キャッシュ生成のためのダミーリクエスト
            response = self.model.generate_content(
                "キャッシュ初期化ダミー（実際の応答内容は重要ではありません）",
                request_options={
                    "caching_config": {
                        "ttl": datetime.timedelta(hours=CACHE_TTL_HOURS)
                    }
                }
            )

            # レスポンスからキャッシュ情報を取得
            cached_content = response.cached_content
            self.cache_name = cached_content.name
            self.expire_time = cached_content.expire_time

            # ローカルに保存
            self.save_cache()

            logging.info(f"新しいキャッシュ作成成功: {self.cache_name}")

        except Exception as e:
            logging.error(f"キャッシュ作成エラー: {str(e)}")
            raise

    def get_active_cache(self) -> str:
        """
        現在有効なキャッシュ名を返す。
        無効であれば新しく作成してから返す。

        Returns:
            str: キャッシュ識別名
        """
        if not self.is_cache_valid():
            self.create_cache()
        return self.cache_name
