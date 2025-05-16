# utils/fileio.py

import os
import json


def read_file(path: str, ext: str = "txt"):
    """拡張子に応じてファイルを読み込む（txt/json）"""
    if ext == "json":
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    elif ext == "txt":
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    else:
        raise ValueError(f"未対応の拡張子: .{ext}")

def write_file(path: str, content, ext: str = "txt"):
    """拡張子に応じてファイルを書き込む（txt/json）"""
    if ext == "json":
        with open(path, "w", encoding="utf-8") as f:
            json.dump(content, f, indent=2, ensure_ascii=False)
    elif ext == "txt":
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    else:
        raise ValueError(f"未対応の拡張子: .{ext}")
