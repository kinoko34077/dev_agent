# test_function_call_dispatch.py

from api.client_functioner import GeminiFunctionClient
from core.function_executor import execute_function_call

client = GeminiFunctionClient()

def run_test(prompt: str):
    print("\n" + "=" * 40)
    print(f"🗣 PROMPT: {prompt}")

    call = client.invoke(prompt)
    if not call:
        print("❌ 関数が選ばれませんでした")
        return

    print(f"🤖 function_call: {call.name}({call.args})")

    result, error = execute_function_call(call)

    if error:
        print(f"❌ 実行エラー: {error}")
    else:
        print(f"✅ 実行結果: {result}")

    response = client.respond_with_result(call.name, result or {"error": error})
    print(f"💬 Gemini応答: {response}")

# ====== 実行テスト ======
run_test("「今日も頑張った」とログに記録して")
run_test("user123 に『報告が完了したよ』と通知して")
run_test("scripts/setup.py を走らせて")
run_test("自己改善モードに入って")
