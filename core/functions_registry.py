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

@dataclass
class RegisteredFunction:
    """登録された関数の完全な情報を保持するクラス"""
    callable: Callable
    schema: Dict[str, Any]  # Function Calling用のスキーマ
    metadata: FunctionMetadata

# 構造化された関数情報を持つ辞書
REGISTERED_FUNCTIONS: Dict[str, RegisteredFunction] = {}

def get_function_schema(func: Callable) -> Dict[str, Any]:
    """
    関数からFunction Calling用のスキーマを生成する
    """
    sig = inspect.signature(func)
    properties = {}
    required = []
    
    for name, param in sig.parameters.items():
        param_type = param.annotation.__name__ if hasattr(param.annotation, '__name__') else str(param.annotation)
        properties[name] = {"type": param_type}
        if param.default == inspect.Parameter.empty:
            required.append(name)
    
    return {
        "type": "object",
        "properties": properties,
        "required": required
    }

def register(
    name: Optional[str] = None,
    tags: Optional[List[str]] = None,
    allow_gpt_call: bool = True,
    allow_edit: bool = False,  # デフォルトは編集不可
    description: str = ""
):
    """
    関数をレジストリに登録するためのデコレーター。

    Args:
        name (str, optional): 関数名。指定しない場合は関数名を使用。
        tags (List[str], optional): 関数のタグリスト。
        allow_gpt_call (bool): GPTからの呼び出しを許可するか。
        allow_edit (bool): GPTによるコード編集を許可するか。
        description (str): 関数の説明文。
    """
    def wrapper(func):
        func_name = name or func.__name__
        
        # メタデータの作成
        metadata = FunctionMetadata(
            tags=tags or [],
            allow_gpt_call=allow_gpt_call,
            allow_edit=allow_edit,
            description=description or func.__doc__ or ""
        )
        
        # スキーマの生成
        schema = get_function_schema(func)
        
        # 関数情報の登録
        REGISTERED_FUNCTIONS[func_name] = RegisteredFunction(
            callable=func,
            schema=schema,
            metadata=metadata
        )
        
        logging.info(f"Function '{func_name}' registered with tags={tags}, allow_gpt_call={allow_gpt_call}, allow_edit={allow_edit}")
        return func
    return wrapper

# -------------------------------
# 🔧 登録関数一覧
# -------------------------------

@register(
    tags=["log", "core"],
    allow_gpt_call=True,
    description="システムログにメッセージを記録します"
)
def add_log(message: str) -> dict:
    logging.info(f"【add_log実行】: {message}")
    return {
        "status": "success",
        "log_saved": True,
        "message": message
    }

@register(
    tags=["script", "core"],
    allow_gpt_call=True,
    allow_edit=False,
    description="指定されたPythonスクリプトを実行します"
)
def run_script(path: str) -> dict:
    try:
        result = subprocess.check_output(["python", path], stderr=subprocess.STDOUT)
        output = result.decode()
        logging.info(f"スクリプト実行成功: {path}")
        return {"status": "success", "output": output}
    except Exception as e:
        logging.error(f"スクリプト実行エラー: {str(e)}")
        return {"status": "error", "message": str(e)}

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