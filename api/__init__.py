# api/__init__.py

# ----------------------------------------
# api モジュール初期化ファイル
# - GeminiClient, OpenAIClient など LLMごとのクライアント群を一括管理
# - api_router により、プロンプト種や負荷で切替可能な構造に拡張可能
# ----------------------------------------

# from . import api_client
# from . import client_gemini
# from . import client_openai
# from . import api_router

__all__ = [
    "client",
    # "client_gemini",
    # "client_openai",
    # "api_router"
]
