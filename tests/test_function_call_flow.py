# tests/test_function_call_flow.py

from Archive.client_functioner import GeminiFunctionClient
from core.function_executor import execute_function_call
from pprint import pprint

from utils.helpers import setup_logger
setup_logger(force=True)  # テストでも logs/system.log に出力

def test_function_call_add_log():
    client = GeminiFunctionClient()

    # Step 1: ユーザーが自然文で依頼
    prompt = "システムログに『自己テスト開始』と記録して"
    print(f"\n📝 Prompt: {prompt}")

    # Step 2: Geminiがfunction_callを返すか確認
    function_call = client.invoke(prompt)
    print("\n📥 Function Call:")
    pprint(function_call)

    assert function_call is not None, "Geminiからfunction_callが返されませんでした"

    # Step 3: ローカルで実行（functions_registryに基づいて）
    result, error = execute_function_call(function_call)
    print("\n⚙️ Execution Result:")
    pprint(result)

    assert error is None, f"関数実行エラー: {error}"
    assert isinstance(result, dict), "戻り値がdictではありません"

    # Step 4: 結果をGeminiに返し、応答を得る
    final_response = client.respond_with_result(function_call.name, result)
    print("\n🔁 Geminiの最終応答:")
    print(final_response)

    assert isinstance(final_response, str) and len(final_response) > 0, "最終応答が空です"

if __name__ == "__main__":
    test_function_call_add_log()
