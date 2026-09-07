# テスト仕様（Phase 0〜alpha0）

既定コマンドは `python -m pytest --collect-only -q` / `python -m pytest -q` とし、`pytest.ini` は `tests/v2/` のみを対象にする。v1 の回帰資料は必要なケースを選び `python -m pytest tests/<case>.py` と明示実行する。

alpha0 の必須カテゴリ:

- protocol の直列化 / validation
- FakeProvider 成功経路
- malformed response
- max_steps 等の有限停止
- event / checkpoint の保存と再起動後の検査
- v1 Runtime 非 import
