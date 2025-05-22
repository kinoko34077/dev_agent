# core/functions_registry.py

import logging
import subprocess
import inspect
from typing import List, Dict, Any, Callable, Optional
from dataclasses import dataclass

# ----------------------------------------
# 関数レジストリ：Function Callingで実行される関数群
# ----------------------------------------

@dataclass
class FunctionMetadata:
    """関数のメタデータを保持するクラス"""
    tags: List[str]
    allow_gpt_call: bool
    allow_edit: bool
    description: str = ""

# 関数レジストリ
REGISTERED_FUNCTIONS: Dict[str, Any] = {}

# 再帰フラグ（非推奨：内的対話システムに移行）
recursion_flag = {"triggered": False}

def register(tags: List[str] = None, allow_gpt_call: bool = True, allow_edit: bool = False, description: str = ""):
    """
    関数をレジストリに登録するデコレータ
    Args:
        tags: 関数のタグリスト
        allow_gpt_call: GPTからの呼び出しを許可するか
        allow_edit: 編集を許可するか
        description: 関数の説明
    """
    def decorator(func: Callable):
        name = func.__name__
        REGISTERED_FUNCTIONS[name] = type('FunctionInfo', (), {
            'callable': func,
            'metadata': FunctionMetadata(
                tags=tags or [],
                allow_gpt_call=allow_gpt_call,
                allow_edit=allow_edit,
                description=description
            ),
            'schema': inspect.signature(func)
        })
        return func
    return decorator

@register(
    tags=["internal", "core"],
    allow_gpt_call=True,
    description="内的対話を開始し、自己改善のための分析と提案を行います"
)
def start_internal_dialogue(topic: str) -> dict:
    """
    内的対話を開始し、自己改善のための分析と提案を行う

    Args:
        topic (str): 対話のトピック（例：「応答方法の改善」）

    Returns:
        dict: 対話結果を含む辞書
    """
    from core.internal_dialogue import InternalDialogue
    
    try:
        dialogue = InternalDialogue()
        response = dialogue.start_dialogue(topic)
        return {
            "status": "success",
            "response": response,
            "topic": topic
        }
    except Exception as e:
        logging.error(f"内的対話開始エラー: {str(e)}")
        return {
            "status": "error",
            "message": str(e)
        }

@register(
    tags=["internal", "core"],
    allow_gpt_call=True,
    description="内的対話を継続し、応答に対する分析と提案を行います"
)
def continue_internal_dialogue(response: str) -> dict:
    """
    内的対話を継続し、応答に対する分析と提案を行う

    Args:
        response (str): 分析対象の応答

    Returns:
        dict: 分析結果を含む辞書
    """
    from core.internal_dialogue import InternalDialogue
    
    try:
        dialogue = InternalDialogue()
        analysis = dialogue.continue_dialogue(response)
        return {
            "status": "success",
            "analysis": analysis,
            "response": response
        }
    except Exception as e:
        logging.error(f"内的対話継続エラー: {str(e)}")
        return {
            "status": "error",
            "message": str(e)
        }

@register(
    tags=["internal", "core"],
    allow_gpt_call=True,
    description="内的対話を終了し、結果のサマリーを取得します"
)
def end_internal_dialogue() -> dict:
    """
    内的対話を終了し、結果のサマリーを取得する

    Returns:
        dict: 対話の結果サマリー
    """
    from core.internal_dialogue import InternalDialogue
    
    try:
        dialogue = InternalDialogue()
        summary = dialogue.end_dialogue()
        return {
            "status": "success",
            "summary": summary
        }
    except Exception as e:
        logging.error(f"内的対話終了エラー: {str(e)}")
        return {
            "status": "error",
            "message": str(e)
        }

@register(
    tags=["notification", "core"],
    allow_gpt_call=True,
    description="指定されたユーザーに通知メッセージを送信します"
)
def notify_user(user: str, message: str) -> dict:
    # 実際にはUI・Webhook・LINE通知などに拡張可能
    logging.info(f"通知: {user}へ → {message}")
    return {"status": "notified", "user": user, "message": message}

def get_function_schemas() -> List[Dict[str, Any]]:
    """
    登録された全関数のスキーマ情報を取得する
    """
    schemas = []
    for name, func_info in REGISTERED_FUNCTIONS.items():
        schema = {
            "name": name,
            "description": func_info.metadata.description,
            "parameters": func_info.schema
        }
        schemas.append(schema)
    return schemas

def get_function_metadata(name: str) -> Optional[FunctionMetadata]:
    """
    指定された関数名のメタデータを取得する
    """
    if name in REGISTERED_FUNCTIONS:
        return REGISTERED_FUNCTIONS[name].metadata
    return None

def can_gpt_call(name: str) -> bool:
    """
    指定された関数がGPTからの呼び出しを許可しているか確認する
    """
    metadata = get_function_metadata(name)
    return metadata.allow_gpt_call if metadata else False

def can_gpt_edit(name: str) -> bool:
    """
    指定された関数がGPTによる編集を許可しているか確認する
    """
    metadata = get_function_metadata(name)
    return metadata.allow_edit if metadata else False