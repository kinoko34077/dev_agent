# dev_agent v2 依存関係メモ

Phase 0 で依存を増やし過ぎないための判断記録。本書は実装計画の補助であり、Provider の採用決定ではない。

## 現行 v1

`requirements.txt` にある次の三つは、既存 v1 の実行・回帰資料を動かすために保持する。

| パッケージ | 用途 | v2 Core への扱い |
| --- | --- | --- |
| `google-generativeai` | Gemini SDK | `providers/gemini` の adapter 境界の外に閉じ込める。Core から import しない |
| `python-dotenv` | v1 の環境変数読み込み | v2 の設定仕様が決まるまで互換用途。Core の必須依存にはしない |
| `pyyaml` | v1 YAML 設定 | v2 の設定形式は未確定。Core の必須依存にはしない |

## v2 Phase 0〜alpha0

### 必須

- Python 3.10 以上
- 標準ライブラリ: `dataclasses` / `typing` / `json` / `uuid` / `datetime` / `pathlib` / `sqlite3` / `argparse` / `hashlib`
- `pytest>=8,<9`（テスト収集・実行）

alpha0 はこの範囲で実装する。Pydantic、ORM、キュー、Agent framework、ベクトル DB は採用しない。型検証が標準ライブラリで不足する場合は、必要な失敗例を先にテスト化し、候補ライブラリを ADR で比較する。

### 追加してよいタイミング

| パッケージ群 | 導入フェーズ | 導入条件 |
| --- | --- | --- |
| Local Provider 用 HTTP / SDK | 4 | adapter の Contract Probe が必要になった時点 |
| Gemini その他 Provider SDK | 5 | Core と分離した adapter 実装・live probe を同時に用意できること |
| schema validation library | 1〜3 の検証結果後 | 標準実装の限界と exit strategy を ADR に記録すること |
| coverage / lint / type checker | Phase 0 の環境整備後 | CI またはローカル再現コマンドを併記すること |

## 再現コマンド

開発環境の初期化:

```bash
python -m venv .venv
.venv\\Scripts\\python -m pip install -r requirements-dev.txt  # Windows
# .venv/bin/python -m pip install -r requirements-dev.txt       # POSIX
```

テスト収集:

```bash
python -m pytest --collect-only -q
```

v2 のテストが増えたら、旧テスト（`tests/`）と v2 テスト（`tests/v2/`）を別ジョブまたは別コマンドで実行し、v1 fixture の失敗と v2 Gate の失敗を混同しない。

## 現環境の確認（2026-09-07）

- Python `3.10.6`
- `PyYAML`、`python-dotenv`、`google-generativeai`、`pydantic` は import 可能
- `pytest` は未導入だったため、`requirements-dev.txt` に追加した
- v2 用の third-party 依存は、alpha0 完了までは増やさない
