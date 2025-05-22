"""
テスト設定を管理するモジュール
"""

import os
from pathlib import Path
from typing import Dict, Any, Optional

class TestConfig:
    """テスト設定を管理するクラス"""
    
    _instance = None
    _config: Dict[str, Any] = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not self._config:
            self._load_default_config()
    
    def _load_default_config(self):
        """デフォルトのテスト設定を読み込む"""
        self._config = {
            "recursion": {
                "enabled": True,
                "max_recursions": 3,
                "recursion_delay": 0.1,
                "retry_count": 2
            },
            "internal_dialogue": {
                "enabled": True,
                "max_history": 5,
                "client": {
                    "name": "test_client",
                    "api_key": "test_key",
                    "model": "test-model",
                    "temperature": 0.7
                }
            },
            "output": {
                "enabled": True,
                "log_level": "DEBUG",
                "save_to_file": True,
                "output_dir": "memory/output"
            }
        }
    
    def get_config(self, section: Optional[str] = None) -> Dict[str, Any]:
        """
        設定を取得する
        
        Args:
            section: 取得する設定セクション（Noneの場合は全設定を返す）
            
        Returns:
            Dict[str, Any]: 設定値
        """
        if section:
            return self._config.get(section, {})
        return self._config
    
    def update_config(self, section: str, value: Dict[str, Any]):
        """
        設定を更新する
        
        Args:
            section: 更新する設定セクション
            value: 新しい設定値
        """
        self._config[section] = value
    
    def reset_config(self):
        """設定をデフォルト値にリセットする"""
        self._load_default_config()
    
    @property
    def output_dir(self) -> Path:
        """出力ディレクトリのパスを取得"""
        return Path(self._config["output"]["output_dir"])
    
    def setup_test_environment(self):
        """テスト環境のセットアップを行う"""
        # 出力ディレクトリの作成
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # 環境変数の設定
        os.environ["TEST_MODE"] = "true"
        os.environ["LOG_LEVEL"] = self._config["output"]["log_level"] 