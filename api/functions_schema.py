FUNCTION_SCHEMA = [
    {
        "name": "add_log",
        "description": "指定されたメッセージをログに追加します。",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "ログに記録する内容"}
            },
            "required": ["message"]
        }
    },
    {
        "name": "run_script",
        "description": "指定されたパスのPythonスクリプトを実行します。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "実行するスクリプトへのパス"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "notify_user",
        "description": "指定ユーザーに通知を送信します。",
        "parameters": {
            "type": "object",
            "properties": {
                "user": {"type": "string", "description": "通知対象ユーザー名"},
                "message": {"type": "string", "description": "送るメッセージ"}
            },
            "required": ["user", "message"]
        }
    }
]
