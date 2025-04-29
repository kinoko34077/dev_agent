# functions_registry.py

import logging

# 再帰トリガ用フラグ
recursion_flag = {"triggered": False}

def add_log(message: str):
    """
    ログにメッセージを追加する関数
    """
    logging.info(f"【add_log実行】: {message}")

def run_script(path: str):
    """
    指定されたPythonスクリプトを実行する
    """
    import subprocess
    try:
        subprocess.run(["python", path], check=True)
    except Exception as e:
        logging.error(f"スクリプト実行エラー: {str(e)}")

def trigger_recursion():
    """
    自己ターンを延長するための再帰トリガ
    """
    from functions_registry import recursion_flag
    logging.info("自己ターン延長トリガーを受信")
    recursion_flag["triggered"] = True

# 実行可能関数マッピング
FUNCTIONS = {
    "add_log": add_log,
    "run_script": run_script,
    "trigger_recursion": trigger_recursion,
}