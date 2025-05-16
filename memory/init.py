# memory/init.py
import os
from utils.fileio import write_file
import datetime

FILES = {
    "system_prompt.txt": "あなたはKiNoTch.の自律エージェントです…",
    "config_snapshot.json": {
        "model_name": "gemini-1.5-pro",
        "temperature": 0.7,
        "system_prompt_version": "v1.0",
        "created_at": datetime.datetime.utcnow().isoformat()
    },
    "summary_combined.txt": "",
    "memory_meta.json": {
        "turns_completed": 0,
        "last_summary_generated": datetime.datetime.utcnow().isoformat(),
        "memory_version": "v1.0"
    }
}

def initialize_memory_context():
    for name, content in FILES.items():
        path = f"memory/context/{name}"
        if not os.path.exists(path):
            ext = "json" if isinstance(content, dict) else "txt"
            write_file(path, content, ext)
