# api/api_router.py

# 不要になるクライアントのインポートを削除
# from api.client_functioner import GeminiFunctionClient
# from api.client_thinker import GeminiThinkerClient
# LLMClient をインポート
from api.client import LLMClient
from core.function_executor import execute_function_call

def process_input(prompt: str) -> str:
    """
    自然文を受け取り、Geminiで通常応答 or FunctionCallingを切り替えて処理する。

    Returns:
        str: 応答テキスト（関数結果含む場合もあり）
    """
    # Step 1: Functionerの役割で試みる
    # func_client = GeminiFunctionClient() # 削除
    func_client = LLMClient(role="function") # 変更
    function_call = func_client.invoke(prompt)

    if function_call:
        result, error = execute_function_call(function_call)

        if error:
            return f"❌ 関数実行エラー: {error}"

        # Step 2: Geminiに結果を返して続きの応答を得る
        # 新しいクライアント（Thinkerの役割）を作成して結果を送信
        response_client = LLMClient(role="thinker") # 追加
        # final_response = client.respond_with_result(function_call.name, result) # 削除
        final_response = response_client.respond_with_result(function_call.name, result) # 変更
        return final_response

    # Step 3: 通常応答（fallback）
    # thinker = GeminiThinkerClient() # 削除
    thinker_client = LLMClient(role="thinker") # 変更
    # return thinker.ask(prompt) # 削除
    return thinker_client.ask(prompt) # 変更