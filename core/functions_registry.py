# core/functions_registry.py

import logging
import subprocess

# ----------------------------------------
# functions_registry.py
# - dev_agent から呼び出されるユーザー定義関数群
# - 【実行】ブロックで呼び出された関数名に対応する処理をここに登録
# - 登録関数は `FUNCTIONS` マッピングに格納され、Executor から呼び出される
# ----------------------------------------

# ✅ Phase 1: 再帰処理用の状態フラグ（mainで参照される）
recursion_flag = {
    "triggered": False,                    # Trueの場合は自己ターン継続
    "injected_input": None                 # 疑似入力（GPTへ再帰的に与える内容）
}


def add_log(message: str):
    """
    ログにメッセージを記録するシンプルな関数。

    Args:
        message (str): 記録したいログメッセージ
    """
    logging.info(f"【add_log実行】: {message}")
    return {
        "status": "success",
        "log_saved": True,
        "message": message
    }

def run_script(path: str):
    """
    指定されたPythonスクリプトをサブプロセスとして実行。

    Args:
        path (str): 実行するスクリプトファイルのパス

    Notes:
        実行結果の標準出力はログには記録されないため、別途stdoutが必要な場合は拡張可。
    """
    try:
        subprocess.run(["python", path], check=True)
        logging.info(f"スクリプト実行成功: {path}")
    except Exception as e:
        logging.error(f"スクリプト実行エラー: {str(e)}")


def trigger_recursion():
    """
    自己ターン（再帰）を継続するためのトリガ関数。

    Notes:
        - `recursion_flag["triggered"]` をTrueに設定し、
          main側で次ターンの自己呼び出しを許可する。
        - `injected_input` はGPTへ与える追加プロンプト。
    """
    # ※ここでのimportは循環参照を避けるためmodule内参照に留める
    logging.info("自己ターン延長トリガーを受信")
    recursion_flag["triggered"] = True
    recursion_flag["injected_input"] = "[再帰モード] 自己改善のための追加提案をお願いします。"


# 🔧 実行可能関数のレジストリ（Executorから動的に呼び出される）
FUNCTIONS = {
    "add_log": add_log,
    "run_script": run_script,
    "trigger_recursion": trigger_recursion,
}
