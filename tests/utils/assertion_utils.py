"""
アサーション用のユーティリティモジュール
"""

from typing import Dict, Any, List
from datetime import datetime
import json
from pathlib import Path

class AssertionUtils:
    """アサーション用のユーティリティクラス"""
    
    @staticmethod
    def assert_dialogue_history(history: List[Dict[str, str]], expected_count: int):
        """
        対話履歴の検証
        
        Args:
            history: 検証する対話履歴
            expected_count: 期待する対話の数
        """
        assert len(history) == expected_count * 2, f"対話履歴の数が不正: {len(history)} != {expected_count * 2}"
        for i in range(0, len(history), 2):
            assert history[i]["role"] == "system", f"システムメッセージのロールが不正: {history[i]['role']}"
            assert history[i+1]["role"] == "internal", f"内部応答のロールが不正: {history[i+1]['role']}"
    
    @staticmethod
    def assert_dialogue_summary(summary: Dict[str, Any]):
        """
        対話サマリーの検証
        
        Args:
            summary: 検証する対話サマリー
        """
        required_fields = ["topic", "duration_seconds", "message_count", "is_active"]
        for field in required_fields:
            assert field in summary, f"必須フィールドが欠落: {field}"
        
        assert isinstance(summary["duration_seconds"], (int, float)), "duration_secondsは数値である必要があります"
        assert isinstance(summary["message_count"], int), "message_countは整数である必要があります"
        assert isinstance(summary["is_active"], bool), "is_activeは真偽値である必要があります"
    
    @staticmethod
    def assert_recursion_state(state: Dict[str, Any]):
        """
        再帰状態の検証
        
        Args:
            state: 検証する再帰状態
        """
        required_fields = ["count", "is_active", "last_error", "dialogue_history"]
        for field in required_fields:
            assert field in state, f"必須フィールドが欠落: {field}"
        
        assert isinstance(state["count"], int), "countは整数である必要があります"
        assert isinstance(state["is_active"], bool), "is_activeは真偽値である必要があります"
        assert state["last_error"] is None or isinstance(state["last_error"], str), "last_errorは文字列またはNoneである必要があります"
        assert isinstance(state["dialogue_history"], list), "dialogue_historyはリストである必要があります"
    
    @staticmethod
    def assert_output_file(output_dir: Path, expected_count: int = 1):
        """
        出力ファイルの検証
        
        Args:
            output_dir: 出力ディレクトリ
            expected_count: 期待するファイル数
        """
        output_files = list(output_dir.glob("dialogue_*.json"))
        assert len(output_files) == expected_count, f"出力ファイルの数が不正: {len(output_files)} != {expected_count}"
        
        for file in output_files:
            with open(file, "r", encoding="utf-8") as f:
                data = json.load(f)
                assert isinstance(data, list), "出力ファイルの内容はリストである必要があります"
                for entry in data:
                    assert "type" in entry, "各エントリにはtypeフィールドが必要です"
                    assert "timestamp" in entry, "各エントリにはtimestampフィールドが必要です" 