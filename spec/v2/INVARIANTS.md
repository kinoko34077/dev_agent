# v2 不変条件

| ID | 不変条件 | Phase 0 の検証方法 |
| --- | --- | --- |
| INV-001 | Provider / model / SDK を失っても Core の契約は成立する | protocol 単体テスト、FakeProvider |
| INV-002 | 全実行に有限の steps / depth / calls / retries / wall time / cost 上限がある | limits / fault tests |
| INV-003 | Task / Step / Status を明示保存し、call stack や global flag を正本にしない | state schema review |
| INV-004 | checkpoint から再起動・resume できる | persistence test |
| INV-005 | request / response / tool / transition / error / budget decision を監査できる | event schema test |
| INV-006 | 正本 repo と最低限の Runtime から診断可能状態へ復旧できる | recovery CLI test |
| INV-007 | 通常 Runtime が壊れても外部 Recovery 経路から修復できる | Rescue drill（後続） |
| INV-008 | Rescue は通常 Runtime / router / vector DB に依存しない | dependency check |
| INV-009 | 自己変更は branch → patch → test → merge / rollback の可逆手順に限る | lifecycle test |
| INV-010 | Hard Budget を Agent の判断で超過できない | governor test |
| INV-011 | Recovery Reserve を通常業務が使い切れない | resource test |
| INV-012 | 支払・credential・公開・不可逆削除・高リスク merge は human approval | policy test |
| INV-013 | Provider 固有 object / schema / event を Core に漏らさない | adapter contract test |
| INV-014 | Provider capability は live probe の観測結果を根拠にする | contract harness（後続） |
| INV-015 | 既知反復作業は Agent loop より Workflow を優先する | resolver test（後続） |
