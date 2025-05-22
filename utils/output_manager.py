import logging
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Union
from enum import Enum, auto

class OutputType(Enum):
    """出力タイプの定義"""
    USER_DIALOGUE = auto()    # ユーザーとの対話
    INTERNAL_DIALOGUE = auto() # 内的対話
    SYSTEM = auto()           # システム設定・状態
    SUMMARY = auto()          # 要約・分析結果

class OutputManager:
    """
    ログ出力を一元管理するクラス
    - 標準出力とログファイルの両方に対応
    - 出力タイプに応じたフォーマット制御
    - 内的対話履歴の保存
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.output_dir = Path("memory/output")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # 出力タイプごとのフォーマット設定
        self.formats = {
            OutputType.USER_DIALOGUE: {
                "prefix": "👤",
                "log_level": logging.INFO
            },
            OutputType.INTERNAL_DIALOGUE: {
                "prefix": "🤖",
                "log_level": logging.INFO
            },
            OutputType.SYSTEM: {
                "prefix": "⚙️",
                "log_level": logging.INFO
            },
            OutputType.SUMMARY: {
                "prefix": "📝",
                "log_level": logging.INFO
            }
        }
        
        # 内的対話履歴
        self.dialogue_history = []
        
    def output(self, 
               message: str, 
               output_type: OutputType,
               data: Optional[Dict[str, Any]] = None,
               save_to_file: bool = False) -> None:
        """
        メッセージを出力する
        
        Args:
            message: 出力メッセージ
            output_type: 出力タイプ
            data: 追加データ（JSON保存用）
            save_to_file: ファイルに保存するかどうか
        """
        format_info = self.formats[output_type]
        formatted_message = f"{format_info['prefix']} {message}"
        
        # ログ出力
        self.logger.log(format_info['log_level'], formatted_message)
        
        # 内的対話の場合、履歴に追加
        if output_type == OutputType.INTERNAL_DIALOGUE:
            entry = {
                "type": output_type.name,
                "message": message,
                "timestamp": datetime.now().isoformat()
            }
            if data:
                entry.update(data)
            self.dialogue_history.append(entry)
            
            # ファイルに保存
            if save_to_file:
                self._save_dialogue_history()
    
    def _save_dialogue_history(self) -> None:
        """内的対話履歴をファイルに保存"""
        if not self.dialogue_history:
            return
            
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = self.output_dir / f"dialogue_{timestamp}.json"
        
        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(self.dialogue_history, f, ensure_ascii=False, indent=2)
            self.logger.info(f"内的対話履歴を保存: {filename}")
        except Exception as e:
            self.logger.error(f"内的対話履歴の保存に失敗: {str(e)}")
    
    def clear_history(self) -> None:
        """内的対話履歴をクリア"""
        self.dialogue_history = []
        self.logger.info("内的対話履歴をクリアしました")

# シングルトンインスタンス
output_manager = OutputManager() 