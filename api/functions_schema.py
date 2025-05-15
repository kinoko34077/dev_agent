# api/functions_schema.py

FUNCTION_SCHEMA = [
    {
        "name": "add_log",
        "description": "指定されたメッセージをログに追加します。",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "ログに記録するメッセージ"
                }
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
                "path": {
                    "type": "string",
                    "description": "実行するスクリプトのパス"
                }
            },
            "required": ["path"]
        }
    }
]
