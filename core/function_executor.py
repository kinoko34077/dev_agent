# core/function_executor.py

from core.functions_registry import FUNCTIONS
import logging

def parse_function_args(args_obj) -> dict:
    """
    Gemini FunctionCallの args を dict に変換（protobuf対応）

    Args:
        args_obj: function_call.args

    Returns:
        dict: {key: value} のPython辞書
    """
    parsed = {}
    for k, v in args_obj.items():
        try:
            parsed[k] = getattr(v, "string_value", v)
        except Exception:
            parsed[k] = str(v)  # fallback
    return parsed

def execute_function_call(function_call: dict) -> tuple:
    """
    Geminiのfunction_callオブジェクトを受け取り、該当関数を実行する。

    Args:
        function_call (dict): {"name": ..., "args": {...}}

    Returns:
        tuple: (成功時: 実行結果dict, エラー時: None, error message)
    """
    try:
        name = function_call.name

        # Geminiは args を protobuf的に返すため、dictに変換
        args = parse_function_args(function_call.args)

        if name not in FUNCTIONS:
            raise ValueError(f"未登録の関数名: {name}")

        result = FUNCTIONS[name](**args)
        return result, None

    except Exception as e:
        logging.error(f"Function実行エラー: {str(e)}")
        return None, str(e)