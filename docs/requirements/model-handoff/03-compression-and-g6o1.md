# Compression boundary and G6O1 split

## Compression Service

Compressionはdev_agent内部の自由なLLM promptではなく、独立HTTP Serviceである。
Humanの明示接続指示により凍結対象から外れ、固定client境界を通常のHandoff one-cycle
compositionから利用できる状態へ進めた。契約は`POST https://api.kinotch.workers.dev/v1/compress`、認証は
`COMPRESSION_API_TOKEN`、profileは`semantic-dense-v1`で、呼出側が渡せるのはPayload
本文と固定profileだけとする。CompressionはG6O1-SIM/LIVEのruntime billingへ接続しない。

Compressionの実live smokeは、2026-09-14時点で現在の実行環境にtokenが無いため
`NOT_VERIFIED`である。local contract/test evidenceとlive service availabilityを混同しない。

Serviceへ渡さないもの:

- instruction、conditions、cautions、requirements、directive（exclusion、focus、
  authority/source、comparison、output contract、continuationを含む）
- authority、approval、budget、privacy、security constraints
- arbitrary system prompt、任意tool、provider固有option

現行実装の `src/dev_agent/compression/` はこの契約のclient境界であり、
`HttpCompressionService.from_environment()`がtokenを明示的に読み、本文と固定profile
だけをPOSTする。通常の`OneCycleDevelopmentLoop` compositionでは、import時I/Oを行わず、
`COMPRESSION_API_TOKEN`が存在する場合だけこのfactoryを遅延呼出しする。tokenが無い、または
設定が利用できない場合、最適化用途はCompressionなしで安全な原文Payloadへfallbackする。
Service本体やProvider選択をdev_agentへ埋め込まない。
`compress_handoff_payload` はControlを保持したままPayloadだけを圧縮し、3,000 Unicode
code points以下は圧縮せず、超過時だけ`semantic-dense-v1`を使う。原文reference、
input/output digest、文字数、Prompt version、model、警告をHandoffへ記録する。
Compression HTTP失敗はboundedなcategoryへ正規化し、最適化用途では原文へ一度だけ
fallbackできる。原文を安全に渡せない場合は呼出側がfail-closedを選べる。

Compression clientの構造上限は1,000,000 Unicode code points、固定Serviceへ送る
provider-safe context limitは200,000 Unicode code pointsで別管理する。後者を超えるPayloadは
HTTP request前に`configuration_error`としてfail-fastし、Serverの拒否待ちや自動連打を行わない。

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

実paid-provider側の作業は、`spec/v2/G6O1_DEFERRED.md` により未検証のまま
凍結し、現在の開発ロードマップに対してはnon-blockingとして扱う。これは
`spec/v2/GATE_STATUS.json` の `G/G6O1 = BLOCKED` を変更せず、Gateの昇格を
意味しない。

本章はCompressionの固定接続境界とG6O1仕様分割を記録する。Compression Serviceは
明示composition時だけ利用し、環境変数の存在だけでProvider poolやG6O1-SIMを
自動activationしない。
