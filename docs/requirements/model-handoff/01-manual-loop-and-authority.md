# Manual development loop and authority

現行の人間主導開発ループは、次の役割境界を持つ。

```text
Human Specification Authority
        ↓ specification / priority
Planner or Analyst
        ↓ analysis / implementation instruction
Executor
        ↓ patch / tests / evidence
Reviewer or Analyst
        ↓ current-state comparison / next plan
Human decision or bounded next cycle
```

Handoffはこの情報伝達を構造化するが、仕様を決める権限をモデルへ移さ
ない。特に以下はHumanまたは既存のprotected authorityに残る。

- protected authority、Hard Budget、Recovery Reserve、credential policy
- paid Providerの解禁、Gate promotion、不可逆操作
- mainまたは公式branchへの無条件promotion
- security、privacy、approval、lease/fencingの緩和

Codex Commanderはdecomposition、ownership、Worker適格性、review、統合を
担当する。Executor Workerは既存DevFarmの狭いmanifestとisolated worktree
だけを受け取り、Worker間で直接通信しない。成功したように見えるモデル出力
は、既存Host VerificationとGit-backed evidenceを通過するまで正式成果ではない。

HandoffのControl（instruction、conditions、cautions、authority情報）と
Payload（分析、監査結果、指示書、成果物説明）を分離する。Compressionへ
渡してよいのは原則Payloadだけであり、Controlを圧縮・再解釈させない。
