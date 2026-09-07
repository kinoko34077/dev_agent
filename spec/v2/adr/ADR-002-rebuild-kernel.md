# ADR-002: Kernel の再構築

- Status: accepted
- Decision: legacy Executor の段階修理ではなく、v2 Kernel を再構築する。
- Reason: 旧 Executor は複数 protocol、再帰、tool、memory、provider を一つの責務に抱える。
- Consequence: v1 は evidence / fixture として扱い、実装は新しい typed boundary から始める。
