# api/api_router.py

from api.client_functioner import GeminiFunctionClient
from api.client_thinker import GeminiThinkerClient
from core.function_executor import execute_function_call

def process_input(prompt: str) -> str:
    """
    自然文を受け取り、Geminiで通常応答 or FunctionCallingを切り替えて処理する。

    Returns:
        str: 応答テキスト（関数結果含む場合もあり）
    """
    # Step 1: Functionerで試みる
    func_client = GeminiFunctionClient()
    function_call = func_client.invoke(prompt)

    if function_call:
        result, error = execute_function_call(function_call)

        if error:
            return f"❌ 関数実行エラー: {error}"

        # Step 2: Geminiに結果を返して続きの応答を得る
        final_response = client.respond_with_result(function_call.name, result)
        return final_response

    # Step 3: 通常応答（fallback）
    thinker = GeminiThinkerClient()
    return thinker.ask(prompt)
