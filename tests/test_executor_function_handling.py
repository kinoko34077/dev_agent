# tests/test_executor_function_handling.py

import logging
import sys
from unittest.mock import MagicMock

# プロジェクトのルートディレクトリをPYTHONPATHに追加（テスト実行のため）
sys.path.insert(0, '.')

# ログ設定（テスト実行時にもログが見えるように）
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ExecutorのFunction Calling処理部分をシミュレートするためのシンプルな関数
def simulate_executor_function_handling(simulated_function_calls, available_functions):
    """
    Executorが受け取ったfunction_callsリストを処理する部分をシミュレートする。

    Args:
        simulated_function_calls (list): ask_with_functionsから返される関数呼び出しリストのシミュレーション。
        available_functions (dict): 利用可能な関数（callableオブジェクトの辞書）。
    """
    logger.info("--- シミュレーション開始 ---")
    final_response = "デフォルト応答（関数呼び出しなし）" # 関数呼び出しがない場合の応答

    if simulated_function_calls:
        logger.info(f"検出された関数呼び出しを処理: {len(simulated_function_calls)}個")
        # function_callsはリストを想定
        for func_call in simulated_function_calls:
            logger.info(f"現在の関数呼び出し: {func_call}")

            # 関数名と引数を安全に抽出
            # Executorのコードに合わせて.get()を使用
            func_name = func_call.get("name")
            func_args = func_call.get("arguments", {})

            logger.info(f"抽出した関数名: {func_name}")
            logger.info(f"抽出した引数: {func_args}")
            logger.info(f"引数argsの型: {type(func_args)}")
            logger.info(f"引数argsの内容 (repr): {repr(func_args)}")


            if func_name:
                # 利用可能な関数かチェック
                if func_name in available_functions:
                    logger.info(f"関数 '{func_name}' が利用可能な関数リストに存在します。")
                    try:
                        # 関数の実行をシミュレート
                        logger.info(f"関数 '{func_name}' 実行をシミュレート中...")
                        # ここで実際の関数 available_functions[func_name](**func_args) を呼び出す
                        # SimpleMockFunctionの実行結果をシミュレート
                        simulated_result = available_functions[func_name](**func_args)
                        logger.info(f"関数 '{func_name}' シミュレート実行完了。結果: {simulated_result}")

                        # 関数実行結果をLLMにフィードバックする部分（ここではスキップまたは簡易化）
                        # Executorのコードでは self.client.respond_with_result(func_name, result) が呼ばれる
                        # テストではこのステップは必須ではないため、ここでは最終応答には直接影響させない
                        logger.info("LLMへの関数結果フィードバックはシミュレーションの範囲外とします。")

                        # 成功した場合の応答（Function Calling後のLLMの最終応答をシミュレート）
                        # Executorのコードでは、関数呼び出しが成功しても最終的なテキスト応答はresponse.textから取得される
                        # ここではシンプルに関数実行が成功した旨を返す
                        final_response = f"関数 '{func_name}' が引数 {func_args} で正常に処理されました。"
                        logger.info(f"シミュレーション結果応答: {final_response}")

                    except Exception as e:
                        logger.error(f"関数 '{func_name}' シミュレート実行中にエラー: {str(e)}")
                        logger.error(f"エラーの詳細: {type(e).__name__}: {str(e)}")
                        # 関数実行失敗時の応答をシミュレート
                        final_response = f"【本文】\nエラー: 関数 '{func_name}' の実行に失敗しました。\n詳細: {str(e)}"
                        logger.info(f"シミュレーション結果応答 (エラー): {final_response}")
                        # エラーが発生したらその関数呼び出しの処理は中止
                        break # 複数の関数呼び出しがあっても、エラーが出たらそこで止める Executorの挙動に合わせる

                else:
                    logger.warning(f"シミュレートされた未登録関数の呼び出し: {func_name}")
                    # 未登録関数呼び出し時の応答をシミュレート
                    final_response = f"【本文】\n警告: 未登録の関数 '{func_name}' が呼び出されました。"
                    logger.info(f"シミュレーション結果応答 (警告): {final_response}")
                    # 未登録関数呼び出しの場合もその関数呼び出しの処理は中止
                    break

            else:
                 logger.warning("シミュレートされたfunction_callに 'name' が指定されていません。")
                 # 関数名がない場合の応答をシミュレート
                 final_response = "【本文】\n警告: 関数名が指定されていません。"
                 logger.info(f"シミュレーション結果応答 (警告): {final_response}")
                 break # 関数名がない場合も処理は中止

    else:
        logger.info("シミュレートされた関数呼び出しはありませんでした。")
        # 関数呼び出しがない場合の応答（最初のデフォルト応答のまま）
        pass # final_response はデフォルトのまま

    logger.info("--- シミュレーション終了 ---")
    return final_response

# テスト用のモック関数
def simple_mock_function_with_args(topic: str):
    """テスト用のシンプルなモック関数"""
    logger.info(f"simple_mock_function_with_args が呼び出されました。topic: {topic}")
    return {"status": "success", "processed_topic": topic}

def another_mock_function(param1: int, param2: str):
     """別のテスト用モック関数"""
     logger.info(f"another_mock_function が呼び出されました。param1: {param1}, param2: {param2}")
     return {"status": "success", "data": f"{param2} ({param1})"}


# --- テストケース ---

# 利用可能な関数リストのシミュレーション
# Executorのself.available_functionsを想定
simulated_available_functions = {
    "start_internal_dialogue": simple_mock_function_with_args,
    "another_function": another_mock_function,
    # 実際にはREGISTERED_FUNCTIONSやsandbox_functionsからロードされる関数が含まれる
}

# ケース1: 正常な関数呼び出し
logger.info("\n--- テストケース1: 正常な関数呼び出し ---")
test_calls_1 = [{
    "name": "start_internal_dialogue",
    "arguments": {"topic": "自分自身について"}
}]
result_1 = simulate_executor_function_handling(test_calls_1, simulated_available_functions)
logger.info(f"テストケース1 結果: {result_1}")
# 想定される出力: "関数 'start_internal_dialogue' が引数 {'topic': '自分自身について'} で正常に処理されました。"

# ケース2: 複数の関数呼び出し（最初の関数が正常）
# Executorは通常1つずつ処理し、Function Callingループ内でrespond_with_resultを呼び出すため、
# ここではFunction Callingのリスト処理自体に焦点を当てる
logger.info("\n--- テストケース2: 複数の関数呼び出し ---")
test_calls_2 = [
    {
        "name": "another_function",
        "arguments": {"param1": 123, "param2": "test"}
    },
    {
        "name": "start_internal_dialogue",
        "arguments": {"topic": "次の話題"} # これは実行されない（前の関数でbreakするため）
    }
]
result_2 = simulate_executor_function_handling(test_calls_2, simulated_available_functions)
logger.info(f"テストケース2 結果: {result_2}")
# 想定される出力: "関数 'another_function' が引数 {'param1': 123, 'param2': 'test'} で正常に処理されました。"

# ケース3: 未登録の関数呼び出し
logger.info("\n--- テストケース3: 未登録の関数呼び出し ---")
test_calls_3 = [{
    "name": "unregistered_function",
    "arguments": {"data": "abc"}
}]
result_3 = simulate_executor_function_handling(test_calls_3, simulated_available_functions)
logger.info(f"テストケース3 結果: {result_3}")
# 想定される出力: "【本文】\n警告: 未登録の関数 'unregistered_function' が呼び出されました。"

# ケース4: 関数呼び出しリストが空
logger.info("\n--- テストケース4: 関数呼び出しリストが空 ---")
test_calls_4 = []
result_4 = simulate_executor_function_handling(test_calls_4, simulated_available_functions)
logger.info(f"テストケース4 結果: {result_4}")
# 想定される出力: "デフォルト応答（関数呼び出しなし）"


# このスクリプトを直接実行すると、上記のテストケースが実行されます。
# Python tests/test_executor_function_handling.py