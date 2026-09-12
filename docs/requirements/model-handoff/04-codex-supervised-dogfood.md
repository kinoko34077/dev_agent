# Codex Supervisor / Dogfood運用

## 目的

Human Specification Authorityの判断を維持したまま、Codexを開発の
Commander / Supervisor / Reviewerとして使い、狭い実装は既存Free Workerへ委譲する。
Worker実行中にCodexが同じ状態を繰り返し読んだりコードを並行生成したりせず、
durable Planだけを残して次の意味ある結果で再開する。

## 契約

既存Commander Planのoptional `supervisor` metadataが、runのroadmap位置、次動作、
bounded heartbeat cadence、wake record、compact metricsを保持する。`advance()`は一回の
bounded passであり、既存DevFarmのdispatch / collect / verifyをcompositionするだけである。

通常経路ではWorker完了だけを理由にHumanへ返さず、HOST_VERIFIED後にCodex reviewを要求する。
Codexが明示承認するまでintegrationしない。

## 待機

待機はLLMのbusy loopではない。`WAITING_FOR_WORKER`をdurableに保存し、外部の既存
呼出し機構がcompact statusを再取得して同じrunをresumeする。cadenceは1 / 5 / 10 / 15分に
boundedとし、同一結果が続く場合でも最大15分を超えない。wakeはWorker terminal、
HOST_VERIFIED、retry上限、integration conflict、Human decisionなど意味のある結果に限定する。

## Reference-first

再取得できるrepository、commit、artifact、evidence、roadmapはPayload本文へ複製せず、
referenceで渡す。外部本文は`ExternalTextReference`のcontent hash、size、created/expiry、
HTTPS locationを持つ場合だけメタデータとして保持する。fetcher/uploaderや外部サービス固有の
authorityはdev_agentへ追加しない。Compression Serviceは未接続であり、G6O1-SIM/LIVEの
runtime証明もこの仕様では行わない。

## 安全境界

- Workerは直接通信しない。
- STATIC_ONLYが既定であり、外部生成コードを無人Host実行しない。
- HOST_VERIFIEDだけで自動merge、push、Gate昇格をしない。
- protected path、Budget、credential、privacy、approval、Recovery、UNKNOWN semanticsを
  Supervisorが所有・緩和しない。
- raw Worker conversation、patch、stdout、stderrをSupervisor metadataへ保存しない。
