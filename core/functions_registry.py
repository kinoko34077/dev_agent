# core/functions_registry.py

import logging
import subprocess

# ----------------------------------------
# 関数レジストリ：Function Callingで実行される関数群
# ----------------------------------------

# ✅ Phase 1: 再帰処理用の状態フラグ（mainで参照される）
recursion_flag = {
    "triggered": False,                    # Trueの場合は自己ターン継続
    "injected_input": None                 # 疑似入力（GPTへ再帰的に与える内容）
}

# ✅ 関数レジストリ本体
FUNCTIONS = {}

def register(name=None):
    """
    関数を FUNCTIONS に登録するためのデコレーター。
    """
    def wrapper(func):
        func_name = name or func.__name__
        FUNCTIONS[func_name] = func
        return func
    return wrapper

# -------------------------------
# 🔧 登録関数一覧
# -------------------------------

@register()
def add_log(message: str) -> dict:
    logging.info(f"【add_log実行】: {message}")
    return {
        "status": "success",
        "log_saved": True,
        "message": message
    }

@register()
def run_script(path: str) -> dict:
    try:
        result = subprocess.check_output(["python", path], stderr=subprocess.STDOUT)
        output = result.decode()
        logging.info(f"スクリプト実行成功: {path}")
        return {"status": "success", "output": output}
    except Exception as e:
        logging.error(f"スクリプト実行エラー: {str(e)}")
        return {"status": "error", "message": str(e)}

@register()
def trigger_recursion() -> dict:
    logging.info("自己ターン延長トリガーを受信")
    recursion_flag["triggered"] = True
    recursion_flag["injected_input"] = "[再帰モード] 自己改善のための追加提案をお願いします。"
    return {"status": "recursion_triggered"}

@register()
def notify_user(user: str, message: str) -> dict:
    # 実際にはUI・Webhook・LINE通知などに拡張可能
    logging.info(f"通知: {user}へ → {message}")
    return {"status": "notified", "user": user, "message": message}