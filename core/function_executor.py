# core/function_executor.py

from core.functions_registry import REGISTERED_FUNCTIONS
import logging
from typing import Tuple, Any, Dict
from utils.output_manager import output_manager, OutputType

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

def execute_function_call(function_call: Dict[str, Any]) -> Tuple[Any, str]:
    """
    関数呼び出しを実行する
    
    Args:
        function_call: 関数呼び出し情報
        
    Returns:
        Tuple[Any, str]: (実行結果, エラーメッセージ)
    """
    try:
        # 関数名の取得
        function_name = function_call.get("name")
        if not function_name:
            return None, "関数名が指定されていません"
            
        # 関数の取得
        function = REGISTERED_FUNCTIONS.get(function_name)
        if not function:
            return None, f"関数 '{function_name}' が見つかりません"
            
        # 引数の取得
        args = function_call.get("arguments", {})
        
        # 関数の実行
        output_manager.output(
            f"関数 '{function_name}' を実行中...",
            OutputType.SYSTEM
        )
        result = function(**args)
        
        output_manager.output(
            f"関数 '{function_name}' の実行が完了しました",
            OutputType.SYSTEM
        )
        return result, None
        
    except Exception as e:
        error_msg = f"関数実行エラー: {str(e)}"
        output_manager.output(
            error_msg,
            OutputType.SYSTEM
        )
        return None, error_msg