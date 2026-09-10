# Provider policy, privacy, hedging, and meta-provider requirements

対象: 添付要件定義書 §15–19

## 15. Provider追加方針

将来の候補は Gemini、Groq、Cloudflare、Mistral、OpenRouter、SambaNova などとする。ただし、候補列挙は実装完了を意味しない。

- 新Providerは src/dev_agent/providers/ 配下へ追加する。
- Adapterの外側へSDK/API固有仕様を漏らさない。
- provider capability、quota、privacy、fallbackの判定は共通のDispatcher/Registry境界で扱う。
- 実Providerの資格情報、利用条件、live evidenceがない場合、Gateを推測でVERIFIEDにしない。

### PROV-001: SDK leakage禁止

Concrete ProviderのSDK型、例外、レスポンス形式はAdapter境界内に閉じる。Controller、ResourceRouter、Budget、TaskGraphへProvider SDKの型を持ち込まない。

### PROV-002: 共通化は適用範囲を限定する

OpenAI-compatible API向けの共通化は、通信部分の重複削減を目的とする場合に限り検討する。Providerごとの認証、モデル名、tool call、usage、エラー、quota挙動は個別probeで確認し、互換性を仮定しない。

## 16. Adapter契約

AdapterはProviderの契約を実行境界へ変換し、少なくとも次を正規化する。

- request/response
- tool call
- usage
- typed error
- timeout/unknown
- capability report

この移行前調整では既存の ModelProvider と ProviderDispatcher の責務を変更しない。Phase 6AのAdapterは注入transportによる契約境界に限定し、Provider固有のheader解析はAdapter内で行った上で、共通経路へは `usage.quota_observation` として正規化する。

## 17. Privacy policy

コスト最適化よりプライバシー制約を優先する。将来のResource/Routerは、Taskのデータ分類とProviderのprivacy policyが適合しない送信を拒否する。

初期分類は次の4段階とする。

| 分類 | 意味 |
| --- | --- |
| public | 外部送信可 |
| normal | 通常のProviderポリシーで送信可 |
| internal | 明示的に許可されたProviderに限定 |
| sensitive / credential | 外部Providerへ送信不可または特別承認が必要 |

分類名だけを追加して外部送信を許可することはしない。現行実装ではTaskの
`sensitivity`をdurable stateからRouter／ModelRequest／AgentBackend admissionへ伝播し、
既存のProvider privacy hard filterと明示承認境界を適用する。Providerごとの詳細な
データ分類、credential専用承認フロー、hedging、Meta Providerは引き続き後段要件であり、
分類値だけで外部送信を許可しない。

## 18. Hedged request

Hedgingは将来の低レイテンシ機能であり、移行前調整の対象外とする。

### HEDGE-001: 通常はsingle dispatch

通常Taskは一つのProvider dispatchを正本とし、重複送信を既定動作にしない。

### HEDGE-002: delayed hedge

将来導入する場合も、遅延、対象Provider、最大数、キャンセル、課金、外部効果のreconciliationを明示した有限状態機械として扱う。

### HEDGE-003: free-only hedge

Free Provider限定のhedgeであっても、quota、inflight、重複tool call、privacy、監査を省略しない。

### HEDGE-004: losing request consumption

勝者以外の送信が消費したquota、token、課金、外部副作用をAuditへ記録する。失敗したとみなして黙って破棄しない。

## 19. Meta Provider

Meta ProviderはProviderを束ねる抽象として将来検討するが、通常経路に二重のRouterを作らない。

- 通常経路のProvider選択は ProviderDispatcher を正本とする。
- Meta Providerは明示的なfallbackまたは互換境界としてのみ追加する。
- upstreamのProvider、model、usage、error、fallbackを可能な範囲でdurable auditへ残す。

## 現行実装への適用境界

Gemini、Groq、Cloudflare Workers AI、Mistral、OpenRouter Free、SambaNovaはProvider-neutralな注入transport AdapterとContractHarness検証まで実装した。さらにGemini／Groq／Cloudflare／SambaNovaには標準ライブラリHTTP Adapterを追加し、Provider固有仕様をAdapter内へ閉じ込めている。Gemini `gemini-3.5-flash-lite` と `gemini-3.8-flash` はthoughtSignature roundtripを含むcanonical live qualification済みで、Cloudflare Workers AIは `spec/v2/evidence/phase6-cloudflare-free-2026-09-09.json` でcanonical live通信、ToolCall往復、durable audit、budget reconciliationを確認済みだが、quota値は未報告である。OpenRouter Freeも `spec/v2/evidence/phase6-openrouter-free-2026-09-09.json` でcanonical live通信とToolCall往復を確認済みである。GroqはHTTP 403、SambaNovaはmodels endpoint HTTP 200後の推論HTTP 429/402、Mistralは推論HTTP 429で未 qualificationである。Mistralの最新失敗証跡は `spec/v2/evidence/phase7-mistral-2026-09-10.json`、SambaNovaの失敗証跡は `spec/v2/evidence/phase6-sambanova-free-2026-09-09.json` と `spec/v2/evidence/phase6-sambanova-gpt-oss-120b-2026-09-09.json` に保存し、free-provider qualification、成功、無償tier、paid worst-caseを推測しない。Task sensitivityの伝播と既存のprivacy hard filter／Backend admissionは実装済みだが、Providerごとの詳細なデータ分類、hedging、Meta Providerは未実装である。Adapter contract／decoderテストをlive qualificationやGate VERIFIEDの証拠に読み替えない。
