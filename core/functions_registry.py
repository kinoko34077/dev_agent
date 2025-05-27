# core/functions_registry.py

import logging
import subprocess
import inspect
from typing import List, Dict, Any, Callable, Optional
from dataclasses import dataclass
import typing

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

# 型名→JSONスキーマ型名のマッピング
TYPE_MAPPING = {
    'str': 'string',
    'STR': 'string',
    'int': 'integer',
    'INT': 'integer',
    'float': 'number',
    'FLOAT': 'number',
    'bool': 'boolean',
    'BOOL': 'boolean',
    'list': 'array',
    'LIST': 'array',
    'dict': 'object',
    'DICT': 'object',
    'None': 'null',
    'NONE': 'null',
    'Any': 'object',
    'ANY': 'object'
}

def signature_to_json_schema(sig: inspect.Signature) -> dict:
    """
    関数のシグネチャをGoogle公式Function Calling仕様に合わせてJSONスキーマに厳密変換
    """
    properties = {}
    required = []
    for name, param in sig.parameters.items():
        if name == 'self':
            continue
        param_info = {}
        # 型ヒントがあればtype情報に追加
        if param.annotation != inspect.Parameter.empty:
            ann = param.annotation
            # typing.Literalの場合はenum化
            if getattr(ann, '__origin__', None) is typing.Literal:
                param_info['type'] = 'string'
                param_info['enum'] = list(ann.__args__)
            # bool型はboolean, intはinteger, floatはnumber, strはstring
            elif ann is bool:
                param_info['type'] = 'boolean'
            elif ann is int:
                param_info['type'] = 'integer'
            elif ann is float:
                param_info['type'] = 'number'
            elif ann is str:
                param_info['type'] = 'string'
            elif ann is dict:
                param_info['type'] = 'object'
            elif ann is list:
                param_info['type'] = 'array'
            else:
                # その他は型名でマッピング
                type_name = ann.__name__ if hasattr(ann, '__name__') else str(ann)
                param_info['type'] = TYPE_MAPPING.get(type_name, 'string')
        else:
            param_info['type'] = 'string'
        # descriptionもdocstringから抽出できれば付与
        param_info['description'] = f"{name}パラメータ"
        properties[name] = param_info
        if param.default == inspect.Parameter.empty:
            required.append(name)
    schema = {
        "type": "object",
        "properties": properties
    }
    if required:
        schema["required"] = required
    return schema

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
        sig = inspect.signature(func)
        json_schema = signature_to_json_schema(sig)
        REGISTERED_FUNCTIONS[name] = type('FunctionInfo', (), {
            'callable': func,
            'metadata': FunctionMetadata(
                tags=tags or [],
                allow_gpt_call=allow_gpt_call,
                allow_edit=allow_edit,
                description=description
            ),
            'schema': json_schema
        })
        return func
    return decorator

@register(
    tags=["logging", "core"],
    allow_gpt_call=True,
    description="指定されたメッセージをログに追加します"
)
def add_log(message: str) -> dict:
    """
    指定されたメッセージをログに追加する

    Args:
        message (str): ログに記録する内容

    Returns:
        dict: 実行結果
    """
    try:
        logging.info(f"【add_log実行】: {message}")
        return {"status": "success", "message": message}
    except Exception as e:
        logging.error(f"add_log実行エラー: {str(e)}")
        return {"status": "error", "message": str(e)}

@register(
    tags=["internal", "core"],
    allow_gpt_call=True,
    description="指定されたトピックに基づいて、内的対話（自己分析・思考）を開始します。曖昧な表現も解釈を試みます。"
)
def start_internal_dialogue(topic: str) -> dict:
    """
    内的対話を開始し、自己改善のための分析と提案を行う

    Args:
        topic (str): 内的対話を行いたい主題。自然な表現（例：「この問題について深く考えて」「応答方法の改善」など）も受け付けます。

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
    description="内的対話を継続し、応答に対する分析と提案を行います。前回の対話の文脈を考慮します。"
)
def continue_internal_dialogue(response: str) -> dict:
    """
    内的対話を継続し、応答に対する分析と提案を行う

    Args:
        response (str): 分析対象の応答。自然な表現や質問形式も受け付けます。

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