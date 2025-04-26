# functions_registry.py

def add_log(message: str):
    """
    ログにメッセージを追加する関数
    """
    from utils.logger import logging  # 必要に応じてlogger使う
    logging.info(f"【add_log実行】: {message}")

def run_script(path: str):
    """
    指定されたPythonスクリプトを実行する（将来sandbox限定予定）
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
