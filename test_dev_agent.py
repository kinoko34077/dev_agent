# test_dev_agent.py

from functions_registry import trigger_recursion
from recursion_manager import RecursionManager

def dummy_main(recurse=False):
    print(f"\n=== ダミー自己ターン開始 (recurse={recurse}) ===")

    if not recurse:
        # 最初のターンで再帰トリガを意図的に発動
        trigger_recursion()

    else:
        # 再帰ターンでは特に何もせず終了
        print("自己ターン再帰実行完了！")

if __name__ == "__main__":
    recursion_manager = RecursionManager(dummy_main)
    dummy_main()
