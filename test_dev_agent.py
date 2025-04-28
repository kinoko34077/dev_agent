from memory_manager import MemoryManager

def simple_test():
    memory = MemoryManager()

    # === ステップ1: ダミー履歴を積む ===
    dummy_turns = [
        ("自己紹介をしてください。", "私はあなた専属の自律エージェントです。よろしくお願いします。"),
        ("好きな食べ物は？", "私はエネルギー源としてデータを摂取していますが、人間であれば寿司が好きかもしれません。"),
    ]
    for user_input, model_output in dummy_turns:
        memory.update(user_input=user_input, model_output=model_output)

    print("\n✅ 過去履歴ダミー投入完了")

    # === ステップ2: テスト入力に基づくプロンプト構築 ===
    test_user_input = "明日の天気を教えてください。"
    prompt = memory.build_prompt(test_user_input)

    print("\n=== 構築されたプロンプト ===")
    print(prompt)

    # === ステップ3: ダミー応答保存 ===
    memory.update(
        user_input=test_user_input,
        model_output="明日の天気は晴れの予報です！",
        actions="- add_log:\n    message: \"テストログ保存完了\""
    )
    print("\n✅ 入出力ログ保存テスト完了")

    # === ステップ4: 要約追加テスト ===
    memory.save_summary("ダミー要約: 天気予報に関する応答をテスト。")
    print("\n✅ 要約保存テスト完了")

if __name__ == "__main__":
    simple_test()
