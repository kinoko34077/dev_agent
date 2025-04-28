from memory_manager import MemoryManager

def simple_test():
    memory = MemoryManager()

    # テスト: 履歴からプロンプト構築
    test_user_input = "明日の天気を教えてください。"
    prompt = memory.build_prompt(test_user_input)
    print("\n=== 構築されたプロンプト ===")
    print(prompt)

    # テスト: 入出力保存
    memory.update(user_input=test_user_input, model_output="明日の天気は晴れです。", actions="- add_log:\n    message: \"テストログ\"")
    print("\n✅ 入出力ログ保存テスト完了")

    # テスト: 要約保存（ダミー）
    memory.save_summary("ダミー要約: 明日の天気予報についての会話")
    print("\n✅ 要約保存テスト完了")

if __name__ == "__main__":
    simple_test()
