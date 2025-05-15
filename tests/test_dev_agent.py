# test_dev_agent.py

# ----------------------------------------
# 自己ターン再帰（RecursionManager + trigger_recursion）の動作検証スクリプト
# 実行すると dummy_main が呼び出され、1回目で再帰トリガを発動 → 自己再呼出しを確認
# ----------------------------------------

from core.functions_registry import trigger_recursion
from core.recursion_manager import RecursionManager

def dummy_main(recurse=False):
    """
    テスト用のダミーmain関数。
    - 初回呼び出し時には再帰トリガを設定
    - 再帰呼び出し時には完了メッセージのみ表示
    """
    print(f"\n=== ダミー自己ターン開始 (recurse={recurse}) ===")

    if not recurse:
        # 最初のターンで意図的に再帰をトリガ
        print("→ trigger_recursion() を発動")
        trigger_recursion()

    else:
        # 再帰後のターンでは処理を終了
        print("✅ 自己ターン再帰実行完了！")


if __name__ == "__main__":
    # 再帰マネージャにdummy_mainを渡して起動
    recursion_manager = RecursionManager(dummy_main)
    dummy_main()  # 初回呼び出し
    recursion_manager.handle_recursion()  # 再帰処理を確認
