# Compression boundary and G6O1 split

## Compression Service

Compressionはdev_agent内部の自由なLLM promptではなく、将来の独立HTTP
Serviceである。初期契約は`POST /v1/compress`で、呼出側が渡せるのは
Payload本文と固定profile（初期値`semantic-dense-v1`）だけとする。

Serviceへ渡さないもの:

- instruction、conditions、cautions
- authority、approval、budget、privacy、security constraints
- arbitrary system prompt、任意tool、provider固有option

応答はcompressed text、profile/prompt version、model、文字数、input/output
digest、warningsを返す。原文は呼出側のreferenceまたはdurable artifactで
保持し、圧縮結果だけをSSOTにしない。

圧縮前後はLLM外で、数値、割合、日付、URL、commit SHA、file path、固有ID、
否定表現の消失を検査する。検査不能時はwarningまたはfail-closedとする。

## G6O1-SIM / G6O1-LIVE

G6O1全体は現行の`BLOCKED_EXTERNAL`判定を変更しない。次の2つを責務分離
して検証する。

| Gate | 対象 | 判定 |
| --- | --- | --- |
| G6O1-SIM | 無料実Providerまたは無料Compression Serviceを、明示的なsimulated-paid bindingと仮想costで実通信し、reserve、worst-case拒否、UNKNOWN、reconciliation、restartを検証 | code/test evidenceで検証可能。ただしG6O1全体を昇格しない |
| G6O1-LIVE | 実paid Providerの請求、丸め、最小請求、失敗時請求、遅延、deployment-owned budgetを確認 | `BLOCKED_EXTERNAL` / `DEFERRED` |

Free、simulated-paid、live-paidは、`provider_binding_id`を含む別identity
として保存する。実際の通信が無料だったことを理由に、simulated-paidの
virtual chargeやUNKNOWNを0円へ変換しない。

G6O1-SIMの必須試験は、正常charge、budget境界、worst-case超過による
dispatch前拒否、timeout/server error後のUNKNOWN charge、reservation保持、
reconciliation、restart後再利用、free/simulated identity分離とする。

本章は仕様分割のみを記録し、Compression ServiceやG6O1-SIM runtimeを
このsliceで自動activationしない。
