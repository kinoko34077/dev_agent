# tests/test_executor_function_calling_chain.py

import logging
import sys
from unittest import TestCase
from unittest.mock import MagicMock, patch, call

# プロジェクトのルートディレクトリをPYTHONPATHに追加（テスト実行のため）
sys.path.insert(0, '.')

# ログ設定（テスト実行時にもログが見えるように詳細に）
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
# Executorや他のモジュールからのログも見えるようにレベルをDEBUGに設定
logging.getLogger('core.executor').setLevel(logging.DEBUG)
logging.getLogger('api.client').setLevel(logging.DEBUG)


# Executorクラス自体をインポート
from core.executor import Executor
# LLMClientクラスをインポート
from api.client import LLMClient


class TestExecutorFunctionCallingChain(TestCase):
    """
    ExecutorのFunction Callingチェーン処理をテストするクラス。
    LLMClientやrun_functionをモックして、特定の応答をシミュレートする。
    """

    # デコレーターによるクラスレベルのパッチは使用しない
    def setUp(self):
        """各テストメソッドの実行前に呼ばれる初期設定"""
        logger.info("\n--- テストセットアップ開始 ---")

        # LLMClient のモックインスタンスを作成
        self.mock_llm_client_instance = MagicMock(spec=LLMClient)

        # Executorインスタンスを生成
        # __init__内で本来LLMClient()が呼ばれるが、今回は後でclient属性を置き換える
        # get_function_schemas_func が呼ばれるので、簡易的なモック関数を渡す
        # ただし、Executor.__init__ が get_function_schemas_func を引数に取らないため、ここは引数なしでOK
        self.executor = Executor()

        # Executorインスタンスが生成された後で、その client 属性をモックインスタンスに置き換える
        self.executor.client = self.mock_llm_client_instance

        # Executorインスタンスの run_function メソッドをモックオブジェクトに置き換える
        # 元のメソッドを保持する必要がなければ、これで十分
        self.executor.run_function = MagicMock()


        logger.info("--- テストセットアップ完了 ---")


    def tearDown(self):
        """各テストメソッドの実行後に呼ばれる後処理"""
        logger.info("--- テストティアダウン開始 ---")
        # setUpで属性を置き換えた場合、ここでは特別な後処理は不要
        logger.info("--- テストティアダウン完了 ---")


    # --- テストケース ---

    def test_function_call_success_returns_text(self):
        """
        ケース1: LLMがFunction Callを返し、関数実行成功後、LLMが最終的なテキスト応答を返すシナリオ
        """
        logger.info("\n--- テストケース1: Function Call成功 -> 最終テキスト応答 ---")

        # シミュレーション用のFunction Callリスト
        simulated_function_calls = [{
            "name": "start_internal_dialogue",
            "arguments": {"topic": "自分自身について"}
        }]

        # mock_llm_client_instance の ask_with_functions メソッドをモック設定
        # api/client.pyのask_with_functionsは(response_text, function_calls)を返す想定
        mock_initial_response_text = "" # Function Calling時は通常テキスト応答は含まれない
        mock_initial_function_calls = simulated_function_calls
        self.mock_llm_client_instance.ask_with_functions.return_value = (mock_initial_response_text, mock_initial_function_calls)

        # self.executor.run_function （setUpで置き換えたモック）のモック設定
        # start_internal_dialogueが呼び出された際に返す結果
        mock_run_function_result = {"status": "success", "processed_topic": "自分自身について"}
        self.executor.run_function.return_value = mock_run_function_result

        # mock_llm_client_instance の respond_with_result メソッドをモック設定
        # LLMが関数結果を受けて最終的なテキスト応答を返すシナリオ
        # Executorが respond_with_result の戻り値の .text 属性にアクセスする可能性を考慮し、
        # モックオブジェクトが .text 属性を持つように設定する
        mock_final_llm_response_object = MagicMock()
        mock_final_llm_response_text = "自分自身についての考察が完了しました。"
        mock_final_llm_response_object.text = mock_final_llm_response_text # .text 属性を追加
        # Executorのコードを見ると respond_with_result の戻り値をそのまま response 変数に代入し、
        # その後 response.text にアクセスしているようです。
        # なので、respond_with_result は .text 属性を持つモックオブジェクトを返す必要があります。
        self.mock_llm_client_instance.respond_with_result.return_value = mock_final_llm_response_object


        # Executorのexecuteメソッドを実行
        user_input = "あなたについて考察して"
        actual_response = self.executor.execute(user_input)

        # --- 検証 ---
        # 1. mock_llm_client_instance.ask_with_functions が一度呼ばれたか
        self.mock_llm_client_instance.ask_with_functions.assert_called_once_with(user_input)

        # 2. self.executor.run_function が正しい関数名と引数で一度呼ばれたか
        self.executor.run_function.assert_called_once_with(
            simulated_function_calls[0]["name"],
            simulated_function_calls[0]["arguments"]
        )

        # 3. mock_llm_client_instance.respond_with_result が正しい関数名と結果で一度呼ばれたか
        self.mock_llm_client_instance.respond_with_result.assert_called_once_with(
             simulated_function_calls[0]["name"],
             mock_run_function_result
        )

        # 4. executeの戻り値が最終的なLLMのテキスト応答と一致するか
        # Executorのコードでは response.text が返されるため、モックオブジェクトの .text と比較
        self.assertEqual(actual_response, mock_final_llm_response_text)

        logger.info("--- テストケース1 正常終了 ---")


    def test_function_call_multiple_calls_in_list(self):
        """
        ケース2: LLMがFunction Callリストに複数の呼び出しを返すシナリオ
        （Executorがリスト内の関数を順次実行するかを検証）
        """
        logger.info("\n--- テストケース2: Function Callリストに複数の呼び出し ---")

                # Function Callリストに複数の呼び出しを設定
        simulated_function_calls = [
            {
                "name": "start_internal_dialogue", # 既存の関数名に変更
                "arguments": {"topic": "Executorのテスト"} # 適切な引数に変更
            },
            {
                "name": "start_internal_dialogue", # 既存の関数名に変更
                "arguments": {"topic": "別の話題"} # 別の引数に変更
            }
        ]

        # mock_llm_client_instance の ask_with_functions メソッドをモック設定
        self.mock_llm_client_instance.ask_with_functions.return_value = ("", simulated_function_calls)

        # self.executor.run_function のモック設定
        # リスト内の各関数が呼ばれるたびに異なる結果を返すように side_effect を使う
        mock_run_function_result_step1 = {"status": "success", "data": "processed data"}
        mock_run_function_result_step2 = {"status": "success", "processed_topic": "次の話題処理済"}
        self.executor.run_function.side_effect = [
            mock_run_function_result_step1,
            mock_run_function_result_step2
        ]

        # mock_llm_client_instance の respond_with_result メソッドをモック設定
        # 各関数実行結果に対するフィードバック後のLLM応答（ここではテキスト応答を返す想定）
        # Executorが respond_with_result の戻り値の .text 属性にアクセスする可能性を考慮し、
        # モックオブジェクトが .text 属性を持つように設定する
        mock_llm_response_after_feedback_object_step1 = MagicMock()
        mock_llm_response_after_feedback_text_step1 = "最初の関数処理が完了しました。"
        mock_llm_response_after_feedback_object_step1.text = mock_llm_response_after_feedback_text_step1

        mock_llm_response_after_feedback_object_step2 = MagicMock()
        mock_llm_response_after_feedback_text_step2 = "次の関数処理も完了しました。最終応答です。"
        mock_llm_response_after_feedback_object_step2.text = mock_llm_response_after_feedback_text_step2

        self.mock_llm_client_instance.respond_with_result.side_effect = [
            mock_llm_response_after_feedback_object_step1,
            mock_llm_response_after_feedback_object_step2
        ]

        # Executorのexecuteメソッドを実行
        user_input = "複数のタスクを実行して"
        actual_response = self.executor.execute(user_input)

        # --- 検証 ---
        # 1. mock_llm_client_instance.ask_with_functions が一度呼ばれたか
        self.mock_llm_client_instance.ask_with_functions.assert_called_once_with(user_input)

        # 2. self.executor.run_function がFunction Callリストの要素数分（2回）呼ばれたか
        self.assertEqual(self.executor.run_function.call_count, 2)
        # 各呼び出しが正しい引数で行われたか
        self.executor.run_function.assert_has_calls([
            call(simulated_function_calls[0]["name"], simulated_function_calls[0]["arguments"]),
            call(simulated_function_calls[1]["name"], simulated_function_calls[1]["arguments"])
        ])

        # 3. mock_llm_client_instance.respond_with_result がFunction Callリストの要素数分（2回）呼ばれたか
        self.assertEqual(self.mock_llm_client_instance.respond_with_result.call_count, 2)
        # 各呼び出しが正しい引数で行われたか
        self.mock_llm_client_instance.respond_with_result.assert_has_calls([
            call(simulated_function_calls[0]["name"], mock_run_function_result_step1),
            call(simulated_function_calls[1]["name"], mock_run_function_result_step2)
        ])


        # 4. executeの戻り値が**最後の** respond_with_result のテキスト応答と一致するか
        # Executorのコード上、Function Call処理ループの後、response は最後の respond_with_result の戻り値になっている。
        # そしてその response.text が return される。
        self.assertEqual(actual_response, mock_llm_response_after_feedback_text_step2)

        logger.info("--- テストケース2 正常終了 ---")


    def test_function_call_run_function_fails(self):
        """
        ケース3: LLMがFunction Callを返し、関数実行が失敗するシナリオ
        """
        logger.info("\n--- テストケース3: Function Call成功 -> run_function失敗 ---")

        # シミュレーション用のFunction Callリスト
        simulated_function_calls = [{
            "name": "start_internal_dialogue",
            "arguments": {"topic": "失敗する話題"}
        }]

        # mock_llm_client_instance の ask_with_functions メソッドをモック設定
        self.mock_llm_client_instance.ask_with_functions.return_value = ("", simulated_function_calls)

        # self.executor.run_function のモック設定（例外を発生させる）
        mock_error_message = "関数実行で意図的に発生させたエラー"
        self.executor.run_function.side_effect = Exception(mock_error_message)

        # Executorのexecuteメソッドを実行
        user_input = "このタスクを実行して（失敗するはず）"
        actual_response = self.executor.execute(user_input)

        # --- 検証 ---
        # 1. mock_llm_client_instance.ask_with_functions が一度呼ばれたか
        self.mock_llm_client_instance.ask_with_functions.assert_called_once_with(user_input)

        # 2. self.executor.run_function が正しい関数名と引数で一度呼ばれたか
        self.executor.run_function.assert_called_once_with(
            simulated_function_calls[0]["name"],
            simulated_function_calls[0]["arguments"]
        )

        # 3. mock_llm_client_instance.respond_with_result は呼ばれないはず (run_functionが失敗したため、Executorのコードを確認)
        # Executorのコードを見ると、run_functionが失敗した場合、exceptブロック内でreturnしているため、
        # respond_with_resultは呼ばれません。
        self.mock_llm_client_instance.respond_with_result.assert_not_called()

        # 4. executeの戻り値がエラーメッセージを含むか
        self.assertIn("【本文】", actual_response)
        self.assertIn("エラー: 関数 'start_internal_dialogue' の実行に失敗しました。", actual_response)
        self.assertIn(mock_error_message, actual_response)

        logger.info("--- テストケース3 正常終了 ---")

    def test_ask_with_functions_fails(self):
        """
        ケース4: LLMClient.ask_with_functions呼び出し自体が失敗するシナリオ
        """
        logger.info("\n--- テストケース4: ask_with_functions失敗 ---")

        # mock_llm_client_instance の ask_with_functions メソッドをモック設定（例外を発生させる）
        mock_llm_error_message = "LLM通信エラー"
        self.mock_llm_client_instance.ask_with_functions.side_effect = Exception(mock_llm_error_message)

        # Executorのexecuteメソッドを実行
        user_input = "何でもいいから応答して（LLMエラー）"
        actual_response = self.executor.execute(user_input)

        # --- 検証 ---
        # 1. mock_llm_client_instance.ask_with_functions が一度呼ばれたか
        self.mock_llm_client_instance.ask_with_functions.assert_called_once_with(user_input)

        # 2. self.executor.run_function は呼ばれないはず (ask_with_functionsが失敗したため)
        self.executor.run_function.assert_not_called()

        # 3. mock_llm_client_instance.respond_with_result は呼ばれないはず (ask_with_functionsが失敗したため)
        self.mock_llm_client_instance.respond_with_result.assert_not_called()

        # 4. executeの戻り値がエラーメッセージを含むか
        self.assertIn("【本文】", actual_response)
        self.assertIn("エラー: LLMとの通信または処理中に問題が発生しました。", actual_response)
        self.assertIn(mock_llm_error_message, actual_response)

        logger.info("--- テストケース4 正常終了 ---")


# このスクリプトを直接実行した場合にテストを実行
if __name__ == '__main__':
    import unittest
    # コマンドライン引数を渡さないように argv=[] を指定
    # unittest.main() はデフォルトで sys.argv を参照するため、
    # Jupyter Notebookなどで実行する際にエラーになるのを防ぐ
    unittest.main(argv=['first-arg-is-ignored'], exit=False)