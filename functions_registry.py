# functions_registry.py

import logging

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

# 実行可能な関数マッピング
FUNCTIONS = {
    "add_log": add_log,
    "run_script": run_script
}
