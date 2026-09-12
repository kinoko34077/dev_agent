# Current State — v2/bootstrap

## Current — 2026-09-12 (Codex DevFarm dogfood v0)

This supersedes the older snapshots below. The implementation slice was
verified at code commit `00d8f19` (the documentation commit that records this
entry advances HEAD afterward).

| Field | Value |
| --- | --- |
| **Branch** | `v2/bootstrap` |
| **Implementation commits** | `ab4c21d` Codex subprocess safety/auth projection; `00d8f19` isolated DevFarm Codex runner |
| **Local regression** | `820 passed, 1 skipped` (`python -m pytest tests/v2 -q`, 222.14s) |
| **Architecture check** | `ARCHITECTURE_PASS` |
| **compileall** | `src recovery scripts` clean |
| **Real Codex CLI** | `codex-cli 0.153.4`; login status confirmed without recording credentials |

### Codex DevFarm proof

The development-only `scripts/devfarm_codex.py::run_codex_attempt()` now
composes the existing manifest, isolated worktree, typed AgentBackend,
Host-side Git authority, and Host Verification boundaries. It derives the
patch from Git using a temporary index, so untracked files are included
without staging the official checkout. It stops at a candidate result;
automatic integration, push, merge, and Gate changes are not performed.

The real CLI read-only smoke returned `SMOKE_OK` in JSONL using an explicitly
projected `auth.json`; no Provider keys or parent configuration were passed.
In the disposable Git fixture, attempt `codex-real-004` used the
`codex-exec` backend and changed only `tests/test_target.py`. Host-side
verification ran the unmodified `tests/test_baseline.py` (`1 passed`) under
`TRUSTED_HOST_EXEC` with explicit attempt approval. The result was
`result_accepted=true`; patch SHA-256 was
`b1b985cee9a71c43ac9554f44c4ece47d74eb9466f43e30c55b7b66e8003d3e7`, elapsed
time was 37,155 ms, and verification ID was
`fe9692c0e92f432793e35f75f16e2f8a`. This is fixture-level dogfood evidence,
not an official branch change or a claim of OS sandboxing: the recorded
containment remains sanitized environment, temporary HOME, bounded
process-tree termination, `network=not_isolated`, and `sandbox=not_provided`.

The Codex adapter's temporary HOME is now reversibly quarantined instead of
being recursively deleted. `--approve-for-me` is an explicit CLI option only
for `workspace-write`; the dangerous full-access/bypass flags remain
unreachable, and the default remains read-only/static unless a caller opts in.

The next Dogfood v0 follow-up is Group D (JSONL normalization, structured
event/usage evidence, and restart/reconciliation helpers). `OS_SANDBOXED`,
automatic integration, and external GitHub required-check configuration
remain separate gates. Existing Gate status is unchanged.

## Current — 2026-09-12 (re-audit closure + Phase 7 latter half A: first concrete AgentBackend adapter)

| Field | Value |
| --- | --- |
| **Current HEAD** | `0606a46` |
| **GitHub Actions exact-head CI** | Confirmed green through commit `0606a46` (`kernel (3.10)`/`kernel (3.11)`/`provider-smoke` all success via `GET /repos/.../commits/0606a46/check-runs`) |
| **Local regression** | `766 passed, 1 skipped` (`python -m pytest tests/v2 -q`, ~168s) |
| **Architecture check** | `ARCHITECTURE_PASS` |
| **compileall** | `src recovery scripts` clean |

### Phase 7 latter half A — CodexExecBackend (commit `f6711b3`)

First concrete implementation of the AgentBackend Protocol. Runs one `codex exec` invocation (or an injected equivalent command) per session as a bounded, non-blocking subprocess; `result()` normalizes only from confirmed process exit code, never the subprocess's own stdout claims; `cancel()` reuses `tools/executor.py`'s `terminate_process_tree`; no `discover()` (an in-memory session map cannot recover across a process restart, so reconciliation correctly falls back to UNKNOWN). Default `codex exec` CLI flags are unverified against a live binary — neither `codex` nor `claude` is installed in this environment — so `command_builder` is injectable and must be confirmed by an operator before production use.

Notable interaction found while building this: `AgentBackendDispatcher.result()` durably persists `AgentBackendStatus.UNKNOWN` as "needs explicit reconciliation" — a plain retry cannot self-heal out of that even if the backend finishes moments later. `CodexExecBackend.result()` therefore blocks (default 300s, overridable per call) before falling back to UNKNOWN, so a normal-length turn resolves within one Dispatcher poll.

### Why this entry exists

A follow-up audit of the prior "Current" snapshot below (HEAD `a052b20`, claiming `v2-core` and `v2 tests` both success) found that claim had never been checked against exact-head GitHub Actions. When actually queried, the branch's most recent CI run at that time (HEAD `643d78e`, one commit before this session started) was **failing**: `v2-core` red on both Python 3.10 and 3.11 (`7 failed, 725 passed`), while `v2 tests` (which only runs a provider-exports smoke test, not the full regression) stayed green — meaning the CURRENT_STATE table's "v2-core success" claim was not evidence-backed. This entry replaces that unverified claim with check-runs-API-confirmed results, and is itself kept current as work continues in the same session rather than left to go stale again.

### Fixed this session (2026-09-12, re-audit)

- **P0-1 — v2-core exact-head regression** (commit `49f1baa`): `test_live_provider_adapters.py`'s `_Response` test stub still had `read(self)` with no arguments, unmatched to the bounded-read contract (`response.read(max_bytes + 1)`) introduced by the P1 HTTP-DoS fix in the prior session. That file had been excluded via `--ignore` during the prior session's local verification, so it passed locally but failed on GitHub Actions. Fixed the stub; searched for and found no other stale `read(self)` stubs.
- **P0-2 — prebuilt Provider instance authority bypass** (commit `287d016`): the P0 endpoint/credential authority fix only validated at `ProviderDefinition.__post_init__` (Factory construction time). A caller could inject an already-built `ModelProvider` instance directly into `ProviderRegistry` or a DevFarm `WorkerAssignment` without ever touching `ProviderDefinition`, or mutate `base_url`/`api_key_env` on a live instance after it passed validation once. Added `validate_provider_instance_authority()` (same SSOT, no independent allowlist) and enforced it at `ProviderRegistry.__init__`, immediately before `ProviderDispatcher.request()` dispatches, and in DevFarm's `_validate_worker_provider()`.
- **P1-1 — HTTP redirect authority gap** (commit `457b049`): every Provider HTTP adapter used raw `urllib.request.urlopen()`, which follows a server's 3xx redirect transparently (including cross-origin) and forwards the `Authorization` header regardless of destination host — silently moving the actual request destination, and the credential with it, outside the validated `base_url` boundary. Added `urlopen_no_redirect()` (a `_NoRedirectHandler` that refuses every redirect) to the shared `openai_compatible/http.py` module and wired it into all 8 concrete provider modules. Verified against a real loopback HTTP server (not a mock): cross-origin and same-origin redirects both rejected, credential never reaches a redirect target (server hit_count stays 1), normal responses unaffected.
- **P1-3 — workflow name/scope mismatch** (commit `00eff74`): `.github/workflows/v2-tests.yml` renamed from `v2 tests` (job `test`) to `v2-provider-smoke` (job `provider-smoke`) — it has only ever run `tests/v2/test_provider_exports.py`, not a full regression; `v2-core` alone owns that.
- **P1-6 — Roadmap Phase representation** (commit `00eff74`): `docs/V2_EXECUTION_PLAN.md` now has an explicit 4-axis breakdown (Implementation Frontier / Operational Acceptance / External Blockers / Next Development Target) so "Phase 7 implementation in progress" and "Phase 6 Operational G6O1 still blocked" can both be stated without contradiction.
- **P0-3 — residual Provider Authority gap** (commit `c98f25d`): the P0-2 fix's `base_url`/`api_key_env` checks alone couldn't catch a hand-written class exposing neither attribute at all while still claiming a network-capable `provider_id` (e.g. "gemini"). Added `APPROVED_PROVIDER_CLASSES` (class-name allowlist, scoped to `NETWORK_CAPABLE_PROVIDER_IDS` only) to `provider_authority_constants.py`; `validate_provider_instance_authority()` now also requires the concrete class to be approved for that identity — except `FakeProvider` (and subclasses), which is exempt unconditionally since it is this codebase's established test-double convention and is pure-Python with no I/O, so it cannot reach a real endpoint regardless of the `provider_id` it simulates. An earlier unscoped attempt at this same check (unconditional, no `FakeProvider` exemption) broke ~46 legitimate test doubles and was reverted; this scoped version required changing only 8 test-local classes that subclassed `ModelProvider` directly instead of `FakeProvider`.
- Also added `permissions: contents: read` to both workflow files (least privilege) and construction-time ISO-datetime validation for `TrustedResourceProfile.verified_at`/`expires_at` in an earlier commit this session.

### Still open (not closed by code alone)

- **Branch protection required status checks**: `v2/bootstrap` ruleset already has deletion/non-fast-forward protection, but no required status checks are configured. **MANUAL ACTION REQUIRED** — this environment's `gh` CLI is not authenticated. Configure in GitHub → Repository → Settings → Branches → `v2/bootstrap` ruleset, using the exact check-run names from a live Actions run (`kernel (3.10)`, `kernel (3.11)`, `provider-smoke`), not the workflow file names. CI itself is fully green; only this enforcement setting is outstanding.
- **G6O1**: real paid-provider worst-case billing proof + deployment-owned budget config (external condition, not a code gap). Whether to scope G6O1 narrowly to a "paid provider activation" gate (so free/local Phase 7 development is not blocked by it) is an open decision, not yet made.
- **OS_SANDBOXED**: declared but unavailable — sanitized env/temp HOME/timeout/process-tree kill is Host containment, not OS-level filesystem/network/process isolation. Unattended external Worker code execution remains disabled.

---

## History / Superseded Snapshots

## 2026-09-12 — BillingResolver DI + full billing match + schema validator (superseded — CI claim unverified)

| Field | Value |
| --- | --- |
| **Current HEAD** | `a052b20` (BillingResolver DI + full billing match + schema validator) |
| **v2 tests** | success (`678 passed, 1 skipped` — all 2 previously-failing free-provider tests now fixed) |
| **v2-core (Python 3.10 / 3.11)** | claimed success — **not independently confirmed against exact-head GitHub Actions at the time this entry was written; a later audit found the branch's actual next CI run (HEAD `643d78e`) was red on v2-core.** See the Current entry above for the verified replacement. |
| **Local regression** | `678 passed, 1 skipped` (`python -m pytest tests/v2 -q`, 198s) |
| **Architecture check** | `ARCHITECTURE_PASS` |

- **P0 — v2-core fix**: `BillingResolver` class introduced as injectable authority. `profile_for()` module function delegates to `_default_resolver` at call-time, so test monkeypatching of `billing_module._default_resolver` propagates correctly to both qualify script and router. The two free-provider qualification tests now pass.
- **P1-2 — Billing full-match**: Router now verifies `price_currency`, `billing_mode`, `overage_policy`, and `no_charge_guaranteed` bidirectionally against catalog profile, not just `cost_minor`.
- **P1-3 — Recovery authority schema**: `legacy_resource_metadata` upgraded to full authority schema validator: `trusted_catalog` resources require `provider_binding_id`, `model_id`, `billing_expires_at`, valid `billing_mode`, and valid `overage_policy` in metadata; remote resources must not carry `privacy_profile="local_only"`.
- **Prior session — P0–P6 security hardening**: billing authority fail-open fix, verification write-order fix, HOST_VERIFIED gate, trust priority, tool_call shortcut removed, schema-based legacy detection.

## 2026-09-11 — configured provider bindings (implementation slice)

- ProviderFactory now exposes separate `ollama_cloud` and `vercel` identities
  over the existing OpenAI-compatible HTTP boundary. Local `ollama` remains a
  separate local adapter and privacy profile.
- `OperationConfig` has an explicit opt-in
  `DEV_AGENT_ENABLE_CONFIGURED_POOL=1` composition path. It reads only
  credential environment-variable names, never credential values, and can
  construct `GEMINI_API_KEY_2` through `_5` as separate Gemini bindings with
  project-scoped quota domains. The primary `GEMINI_API_KEY` remains supported.
- Ollama Cloud requires `OLLAMA_CLOUD_MODEL`; Vercel AI Gateway requires
  `AI_GATEWAY_MODEL`. Their API keys alone do not select a model or create
  qualification evidence.
- `TrustedResourceProfile` now records `billing_mode` and optional allowance
  metadata. Gemini/Cloudflare allowance-backed entries are distinguished from
  fixed-free local/OpenRouter entries. Ollama Cloud and Vercel are not inserted
  as `cost_minor=0`; an exact model billing/qualification profile is still
  required before production routing.
- This is provider construction/configuration support, not live qualification.
  No new binding is marked qualified merely because an environment variable is
  present. Live qualification must use the existing canonical
  Controller → ProviderDispatcher path and exact binding/model evidence.

## 2026-09-11 — P0/P1 immutability and billing hardening slice

- `tests/v2`: `660 passed, 1 skipped` (local; live provider tests excluded).
  `scripts/check_architecture.py`: `ARCHITECTURE_PASS`.
- Exact HEAD: `2796294`. CI on this commit: `v2-core` and `v2 tests` both
  succeeded. The stale references to `f438d79` / `2b8f247` as "current baseline"
  in earlier entries below are now superseded by this entry.
- P0-1 fix: `_write_immutable_text` now uses `os.link()` instead of
  `os.rename()` so that an existing destination raises `FileExistsError` on
  POSIX/Linux and Windows identically. `os.rename()` on Linux silently replaces
  the destination; this made immutable attempt artifacts re-writable on GitHub
  Actions (Linux).
- P0-2 fix: Verification records are now written to an append-only directory
  (`attempts/<id>/verification/<verification_id>.json`) instead of a single
  `verification.json` file. Each call to `_write_verification_record` assigns a
  new UUID and returns it. `_list_verification_records` returns all records
  sorted by `verified_at`. This allows `STATIC_ONLY → TRUSTED_HOST_EXEC`
  escalation without overwriting earlier evidence. The Commander reads the
  verification directory and selects the strongest qualifying record.
- P0-3 fix: Resource metadata now stores `billing_verified_at` and
  `billing_expires_at` from the trusted catalog at projection time. The
  `ResourceRouter` re-checks `billing_expires_at` at every dispatch so a
  long-running process cannot route to a resource whose billing authority has
  expired. `repair.py` includes these fields in `_safe_projection`, `after`,
  and `apply_resource_repairs`.
- P1-1 fix: `repair.py` now includes `allowance_amount`, `allowance_currency`,
  and `allowance_period` in `_safe_projection` and the `after` dict. During
  `apply_resource_repairs`, stale allowance keys are removed before re-projecting
  so that catalog renames and removals apply cleanly.
- P1-3 fix: `v2-core.yml` CI now runs `python scripts/check_architecture.py`
  as a required step (3.11 only, before `compileall`).

## 2026-09-11 — critical Worker hardening slice

- External Worker Host Verification is fail-closed by default:
  `STATIC_ONLY` validates patch/path/Git evidence without executing patched
  code. `TRUSTED_HOST_EXEC` requires explicit per-attempt operator approval;
  `OS_SANDBOXED` is declared but unavailable. Unattended external Worker code
  execution therefore remains disabled.
- Host commands are strict `pytest`/`compileall` structures with relative
  worktree targets and bounded safe options only. Environment, output, total
  verification budget, and target resolution are bounded; stdout/stderr are
  secret-sanitized. Worker-owned test changes alone cannot yield acceptance.
- Verification writes immutable attempt evidence containing patch, manifest,
  test-spec, base-revision, containment, and verified-test digests. Commander
  integration reads that evidence rather than the mutable latest projection.
- Allowance billing is not inferred from `cost_minor=0`; current trusted
  billing mode, overage policy, and no-charge guarantee are required. Failed
  planner dependencies are durably terminalized.

- External Worker hardening: verification defaults to `STATIC_ONLY`; external
  patches are not executed on the Host unless an operator explicitly approves
  that attempt as `TRUSTED_HOST_EXEC`. `OS_SANDBOXED` is defined but not yet
  available, so unattended external Worker code execution remains disabled.
  Host test commands use a strict pytest/compileall allowlist, targets are
  resolved inside the worktree, output is bounded and secret-sanitized, and
  Worker-owned test changes alone cannot produce an accepted result.
- Billing hardening: `cost_minor=0` is not sufficient evidence of no charge.
  Current trusted profiles carry billing mode, overage policy, and a durable
  no-charge guarantee; allowance-backed resources without a hard-stop guarantee
  are fail-closed. Verification records bind immutable attempt, manifest, test
  specification, containment, and patch SHA-256 evidence.

実装基準は `f438d79` です。直近のローカル全回帰もこのコード基準で検証し、
本書はそのコードと、直近の外部資格化・DevFarm実行結果を同期したCurrent Stateです。
GATE_STATUSの既存statusは変更していません。

今回の軽量化リファクタでは、Qualification Catalogのsession内共有、planning snapshotの再利用、
lease fencing primitiveの中立化、Operationのbootstrap／planning／CLI／control repository分離、
Provider／Intelligenceのlazy export、ProviderFactoryの遅延構築、Controllerのphase helper分離、
ResourceLedger schema migrationの分離、architecture dependency check、affected-test mapを追加しました。
外部API、Task lifecycle、Budget／Quota、Approval、Lease/Fencing、UNKNOWN／Reconciliation、
Provider hierarchy、Gate判定は変更していません。refactorの性能比較は
`docs/superpowers/plans/2026-09-11-lightweight-refactor.md`に記録しています。

## 判定

- Phase 6 foundation: `VERIFIED`
- Phase 6 operational: `G6O2`〜`G6O6` は `VERIFIED`
- `G6O1`: `BLOCKED_EXTERNAL`（実paid Providerのworst-case課金実証と、deployment-owned budget設定の外部保護が必要）
- Phase 7A/B: typed task profile、bounded tier policy、明示opt-in resource routing、model identityとthinking effortの分離を実装済み
- Phase 7C/D: deterministic host evaluator、durable evidence、有限escalation plan、明示review、dispatch-ready handoffを実装済み
- Phase 7 execution: `EscalationExecutor`がaccepted `dispatch_ready`を再検証し、既存ProviderDispatcher・effect intent・budget/resource境界を通る有限dispatchを実装済み。`EvaluationDispatchCoordinator`がhost evaluator→明示review→dispatchの一回のcycleを接続し、PASS／拒否／unknownを別状態で返す。重複再送とunknown/reconciliationをfail-closedに扱う
- Phase 7E: bounded workflow promotion proposalの生成境界を実装済み。自動promotionは行わない
- Phase 7 lifecycle: host evaluator／reviewed dispatchの結果を、`TaskLifecycleCoordinator`が冪等な`commit_transition()`でterminal／retry／approval／reconciliation状態へ適用する境界を実装済み
- Phase 7 finite lifecycle: `FiniteLifecycleLoop`が既存のevaluator／review／dispatch／lifecycle境界を明示的な有限cycleへ合成する。評価回数上限を持ち、review・evidence・dispatchは呼出側が供給し、自動承認・自動再送・モデル自己昇格は行わない
- Phase 7 integration: CommanderのWorker proposalはmanifestに固定されたGit commit objectを読み、作業中のcheckout HEAD進行に影響されない。通常のcode dependencyは依存Taskの`INTEGRATED`までreleaseせず、FiniteLifecycleのcycle使用数はdurable evaluation historyから再構築する
- Phase 7 evidence routing: `EvidenceBasedRoutingPolicy`がhost-verified Worker metricsを、最小sample数・証拠期限・受入率／retry rollback条件付きで、呼出側から渡されたhard-filter済みbindingの範囲だけで順位付けする。証拠不足・期限切れ・回帰は採用せず、ResourceRouterのcapability／privacy／quota／budget hard filterや通常routingを上書きしない。自動routingへの接続は未実施
- Phase 7 Operation Layer: `python -m src.dev_agent` の`start`／`submit`／`status`／`stop`を追加し、既存のSQLiteStateStore・DurableQueue・WorkerRunner・Controller・ProviderDispatcherをcompositionした。StateStoreとQueueは同じSQLiteファイルを共有し、CLI停止は実行中Taskを即時失敗扱いせず、durableな協調キャンセル要求または既存のreconciliation状態を維持する
- Phase 7 Operation lifecycle composition: `OperationService.evaluate_task()`／`review_task()`／`dispatch_reviewed()`を追加し、既存の`FiniteLifecycleLoop`、`EvaluationCoordinator`、`TaskLifecycleCoordinator`、`EscalationExecutor`、`ProviderDispatcher`を明示review境界のまま接続した。reviewed dispatchは`DurableQueue`のlease proofでeffect intent、Provider dispatch、Task transitionをfenceする。自動昇格・自動承認・新Schedulerは追加していない
- Phase 7 root planning boundary: `RootPlanningProposal`／`RootPlanningValidator`を追加し、reasoning rootからの有限child proposalをTask作成前に検証する。TaskGraph上限、dependency cycle、未知capability、protected worker assignment、sensitivity downgradeをfail-closedで拒否し、依存childは既存StateStoreへ`WAITING_DEPENDENCY`として保存する。Plannerはauthorityを発行しない
- Planner dependency lifecycle: 依存childは既存Operation maintenance境界で前段Taskの完了を再評価し、全依存が`COMPLETED`のときだけ既存Durable Queueへreleaseする。失敗／取消依存はchildを実行せずterminalizeし、独立Schedulerは追加していない
- Planner dependency types: 既存依存は後方互換の`TASK_COMPLETED`として扱い、新規proposalは`ARTIFACT_READY`、`TASK_COMPLETED`、`CODE_INTEGRATED`を依存ごとにdurably保存する。`CODE_INTEGRATED`は`integration_status=INTEGRATED`と非空`integration_revision`の両方を要求し、完了だけではreleaseしない
- Late provider completion: timeout後も生存するprovider callの結果を同じdurable effect intent／budget reservationへ一度だけreconcileし、既知の成功応答は新しいProvider requestなしでControllerが保存済み応答をreplayできる。`Operation.maintenance_tick()`はreconciliation済みの待機TaskだけをQueueへwakeし、結果不明は`WAITING_RECONCILIATION`に留める
- Queue attempt accounting: statusの`current_attempt`／`execution_attempts`は実行回数、`claim_count`／`claim_streak`はlease claimの統計・crash-loop fenceとして分離し、waiting・wakeだけではlogical execution budgetを消費しない
- Phase 7 hierarchy Operation E2E: 通常の`OperationService` compositionで、L1 primary failure→別quota domainのL1 fallback→明示review済みL2 dispatch→terminal completionを確認する。各dispatchはexact current tier、既存effect identity、lease proof、Task／Queue lifecycleを通過する。同一UNKNOWN quota domainのadmissionを無制限に再利用しない境界も維持する
- Operation hardening: Operation起動時の既存Resourceはread-onlyで保持し、binding×model×trusted catalogにない価格を無料と推測しない。Cloud Resourceはoperator-ownedな`quota_domain`を明示し、初回はlive probeなしでhealthy扱いせず、正常Provider応答／正常quota probeだけがResource freshnessを更新する。`DispatchDenied`はbudget／quota／maintenance／resource wait／invalid failureへ意味別に遷移し、canonicalなrate-limit／quota ProviderErrorも`BLOCKED_QUOTA`へparkする
- Operation quota maintenance hardening: 同一quota domainに複数Resourceがある場合も、正常な観測がbounded probe枠を消費しないよう、期限到来したblocked Resourceだけを選択して一回probeする。reset境界前、authorization／permission、invalid observationはprobe対象にしない
- DevFarm admission hardening: proposal段階の各外部Providerを送信直前に再検証し、operator activation、期限内capability qualification、正確なprovider binding／model／L1 tier、trusted no-charge billingをすべて満たさないProviderをfail-closedで拒否する。Provider名だけのfree判定や、直接注入された未資格Providerによる迂回を許可しない
- Intelligence hierarchy evidence: `tests/v2/test_phase7_hierarchy_e2e.py`で、失敗したL1 bindingから同Tierの別L1 bindingを先に試し、その後に明示承認されたL2へ有限に昇格するcanonical ProviderDispatcher経路を確認した。各dispatchは今回のexact tierをhard filterし、unknown effectは再送しない
- Cross-process safety hardening: cancellation requestはappend-onlyの`task_controls`へ保存し、terminal transition直前に再読込してlate completionをfenceする。Provider healthはresource/binding単位、quota wakeは`quota:<domain>`単位で、別Resource／別domainのTaskを誤って起こさない
- Capability and waiting hardening: QualificationResolverは資格化証拠からcanonicalな`text`／`tool_call`等だけをrouting projectionへ導出し、`architecture`等のTask competency／policy traitをProvider capabilityへ渡さない。submit／Policy入口では未知capabilityを永続化前にfail-fastし、binding単位のprovider execution saturationは同じlaneの待機Taskをwakeし、全eligible lane飽和時のpool waitは任意laneの復帰で一回だけ再選択する。late completionは同じintent／reservationへ一度だけreconcileし、既知成功を保存済み応答のreplayへ戻す
- Residual hardening: `dev-agent resource validate`／`resource migrate --apply --operator-ref ...`で旧Resourceを通常起動から分離した明示repairへ送り、schema v10の`resource_repairs`へbefore/after auditを保存する。Qualification routing admissionはcurrentかつ`high` confidenceだけを許可し、low/mediumは観測として保持してもProduction routeへ投影しない。複数bindingが全て飽和した場合はpool waitへparkし、任意lane復帰で一回だけ再選択する
- Phase 6 quota operation: ResourceLedger schema v10でmetric／unit／window／reset source／blocked-until／block reasonとbounded unknown-quota admission、明示Resource repair auditを保持し、ProviderErrorの429／quota／transport分類をrouting blockへ接続済み。blocked observationは新しい正常観測で明示的に復帰する。Scheduler queue schema v4と`QuotaWakeScheduler`はreset boundaryへのdurable parking／wakeを提供し、`QuotaRequalificationCoordinator`は呼出側が明示した一回のbounded probeについて、freshな正常観測の保存後だけdue taskをwakeする。Operation Layerの`maintenance_tick`がdue domainだけを対象にprobe上限を適用し、`start`／`start --once`からも同じmaintenance boundaryを通る。OpenAI互換adapterはtelemetryを返す場合だけ既存`/models` probeからquota observationを返し、typed probe failureには保守的cooldownを永続化する。Provider再probeの無制限loopやclockだけによるblock解除は行わない
- DevFarm orchestration: Remote proposalとHost verificationを分離し、remote inference枠とworktree verification枠を別Governorでboundedに制御する。proposal失敗時にworktreeを作成せず、自動mergeもしない。Host Verificationはsanitized environment、temporary HOME、bounded output、timeout時のprocess-tree終了を持つが、OS filesystem/network sandboxではない
- Development Commander: `scripts/devfarm_commander.py`が既存DevFarmの上にdevelopment-only親Planを提供する。`.devfarm/plans/<run-id>.json`へobjective、base revision、Task、依存、非重複ownership、assignment、result参照をdurably保存し、plan／dispatch／status／collect／verify／resume／reassign／mark-integratedを既存境界のcompositionで提供する。Taskごとの固定revisionを許容し、code dependencyは明示的な`mark-integrated`後だけreleaseする。非自明なGoalではCodexが分解・依存・ownership・risk・Worker適格性を先に記録し、狭いpatch/test/docs等を原則Worker候補とする。Codex担当へ残す場合も理由を記録する。Production Runtimeのstate／Scheduler／authorityやAgentBackendではない
- Commander dogfood: `phase7-commander-local-dogfood-004`で、`aa2f819`固定のproposal、隔離worktreeでのHost Verification（許可済みfocused test `1 passed`）、Codex review、明示integrationを一連のPlanとして完了した。これはCommanderの計画・依存・検証・統合境界の実証であり、外部Cloud Workerの資格化や成功を意味しない
- Commander external Worker dogfood: 2026-09-11にGemini L1、Cloudflare L1、OpenRouter Freeへ、単一の非保護focused testだけをmanifest-scopedでbounded proposalした。実通信・Provider metricsは取得できたが、proposalはpatch hunk行数不一致でHost Verification前に決定的拒否となり、valid patch・Host Verification・Git integrationの成功証拠には数えていない。外部Workerのpatch生成品質は未完了として保持する
- 最新のCommander実Cloud再試行（`hardening-cloud-l1-007`）では、Gemini `gemini:worker` のqualification／billing／operator activationは送信前検証を通過したが、実通信がWindowsソケット境界の`WinError 10013`でproposal前に失敗した。patch、Host Verification、Git integrationは生成されておらず、成功証拠には数えない。外部送信を伴う再試行には、送信対象をさらに合成データへ限定するか、operatorの明示確認が必要
- 最新のローカル全回帰（P0/P1 hardening slice後）: `660 passed, 1 skipped`（live provider tests除外。`python -m pytest tests/v2 -q`の全回帰は230秒程度）
- `hardening-cloud-l1-008`では、既存ソースを送らない空のoutbound scopeでGemini `gemini:worker`／`gemini-3.5-flash-lite`へdocs-only proposalを実行し、1,870ms・usage（prompt 1,021 / candidate 322）を記録した。patchは許可された新規docs 1ファイルのみで、隔離Host Verificationは`5 passed`、`result_accepted=true`となった。Codexはpatchを`34bfef8`へGit-backed integrationした。Commander planの`INTEGRATED` writebackは、保護された`.devfarm`状態への追加承認が必要なため未確定として扱う
- Gate昇格やlive qualificationの成功は、local test・model自己申告・Worker proposalだけから推測しない

## 検証

- v2ローカル全回帰（provider binding slice）: `651 passed, 1 skipped`（`python -m pytest tests/v2 -q`、167.38秒。所要時間は実行環境依存）。Windows ACLはdeployment-owned skip。`636 passed, 1 skipped`以前は履歴baselineとして保持する
- DevFarm admission／hierarchy focused: `54 passed`（qualified bindingの送信前再検証、未資格model拒否、L1 alternate→L2横断証拠を含む）
- 追加監査focused: Provider quota分類／DevFarm model-qualified activation／trusted free qualificationを含む`45 passed`
- Operation hardening focused: `94 passed, 1 skipped`（Operation、quota、DevFarm attempt、SQLite contention、security、budget境界）
- Operation Layer focused: `12 passed`（submit／status、canonical Dispatcher経由のstart、queue復旧、process restart、terminal／waiting reconciliation、provider非依存safe stop、durable cancellation request、due quota maintenance／wake、startからのmaintenance境界）
- Evaluator→dispatch cycle focused: `18 passed in 2.62s`
- finite lifecycle focused: `11 passed`（明示review、dispatch、terminal transition、評価cycle上限、process restart後のdurable cycle／waiting boundary）
- intelligence routing / escalation execution focused: `26 passed in 1.28s`
- DevFarm manifest / patch / host verification focused: `24 passed in 27.50s`
- Commander focused: `4 passed`（親Plan、ownership／dependency validation、dispatch／collect／Host Verification、bounded reassign、CLI status）
- Commander dogfood: `phase7-commander-local-dogfood-004`のWorker成果をHost Verified後にCodexが明示統合。host testは`1 passed`、metricsは`provider_id=local-harness`のdurable artifactへ記録。実Cloudの`hardening-cloud-l1-008`はGemini L1 proposal、Host Verification（`5 passed`）、metrics、Git-backed integration commitまで完了したが、Commander planの保護状態writebackは未確定
- Operation external E2E: Cloudflare `@cf/meta/llama-3.1-8b-instruct`で`submit`、`start --once`、ToolCall／ToolResult、final response、durable `task.completed`、provider audit成功2件を確認。証跡: [`phase7-operation-cloudflare`](../spec/v2/evidence/phase7-operation-cloudflare-2026-09-10.json)
- Phase 7 integration acceptance: Commander parallel baseline、`INTEGRATED` dependency、FiniteLifecycle restart、quota reset→bounded probe→wake、Operation external E2E、Commander dogfoodを確認済み。Evidence routingは実Provider Worker metricsのminimum sample／freshness／rollback証拠が揃うまで`DEFERRED_ADVISORY`とし、ResourceRouterへhard接続しない。実AgentBackend adapter／MCPはこの条件の完了後に着手する
- AgentBackend boundary: `src/dev_agent/backends/protocol.py`に外部Agent harnessのidentity、scoped request、session、event stream、cancellation、completion／failure／unknown／reconciliation resultを定義し、`dispatcher.py`に既存authorityの証拠を束ねる`BackendAdmission`を追加した。Task／Backend identityのrequired capability coverage、strict-`True` authorization、scope／budget／approval／lease／privacy証拠をstart前に検証する。実Codex App Server adapter、MCPは未実装で、既存Runtime／State／Scheduler／Budget／Recoveryの所有権を移していない
- Execution target seam: `intelligence/target.py`の`ExecutionTargetPolicy`がModelProviderとAgentBackendを分離する。通常はminimum sufficient ModelProviderを選び、AgentBackendは明示autonomyと既存のapproval／budget／privacy／capability証拠が揃った場合だけ選択する。L3というだけでAgentBackendへ自動昇格しない
- Operation multi-provider: `OperationConfig.provider_pool`で複数bindingを`ProviderFactory`／`ProviderRegistry`へcompositionし、production routingではexact current tierをhard filterしたうえで同Tierの別bindingへbounded fallbackする。一次bindingのrate-limit後に別L1 bindingへ切り替えるE2Eを確認した
- AgentBackend focused: protocol `7 passed`、dispatcher `23 passed`、intelligence target `2 passed`、Operation pool fallback `1 passed`を含む。dispatcherではidentity復元、scope／authority／capability拒否、lease／budget／approval／privacy admission、session重複防止、event sequence、cancel、UNKNOWN、restart、reconciliationを検証した
- Evidence routing focused: `5 passed`（minimum samples、hard-filter済みbinding限定、期限切れ、rollback threshold、malformed evidence拒否、latest timestamp）
- DevFarm host verification: Gemini 3.5 Flash-Lite `gemini-worker-phase7-003` が、入力ファイルを外部送信せず、隔離worktreeへpatchを適用し、許可済みhost test `7 passed` を確認
- DevFarm 2 Worker並列: `gemini-worker-parallel-a` と `gemini-worker-parallel-b` が別worktree・別所有ファイルで同時実行され、各 `7 passed`、`result_accepted=true` を確認。実測はそれぞれ1.528秒、1.278秒
- Worker metricsはhost側で `provider_id`、`provider_binding_id`、`model_id`、`intelligence_tier`、`task_type`、request id、elapsed、許可されたusage scalar、host test結果を記録し、`.devfarm/metrics.sqlite3`へ`task_id + request_id`単位で冪等に蓄積する。Modelのtests claimは証拠に採用しない。metricsはrouting候補の観測値であり、Policyやacceptanceを上書きしない
- Commander/Worker履歴 hardening: Commander Planは`plan_revision`付きCASで並行更新を検出し、Worker結果は`attempt_id`ごとのimmutable artifactと履歴を正本とする。検証時はrootの最新投影へフォールバックせず、選択attemptのpatchを必須として読む
- Protected policy / audit hardening: protected responsibility path、PathPolicyの最長prefix、secret semantic sanitizerを共有境界へ集約し、token使用量・session telemetryは保持しつつcredential値だけをredactする
- quota/reset focused regression: 既存のquota policy、schema v10 migration、明示Resource repair audit、blocked routing、bounded typed probe failure、DevFarm remote/host concurrencyに加え、同一domain内のdue Resource選択回帰を全回帰へ追加
- Provider alias focused regression: OpenAI互換／Cloudflareのbackend-reported model aliasを要求bindingへ正規化する回帰を含む`15 passed`。実Providerの応答内容はtelemetryへ分離し、外部Workerのpatch成功とは別に扱う
- SQLite contention: 独立processのqueue／state／stop／status同時操作、WAL、5秒bounded busy timeoutを確認。既存のWindows ACL skipは継続
- skip: `tests/v2/test_budget_reservations.py:142`（Windows ACLはdeployment-owned）
- 最新コード基準のexact-head GitHub Actionsは、push後に`v2-core`（Python 3.10/3.11）と`v2 tests`を外部観測する。repo内GATE_STATUSへCI結果を書き戻してexact-headを自己参照しない
- exact-head CI evidence: `929651cf3f16c6422e3e4d7178e48a6e88627878`に対し、`v2 tests` run `34539178663` と `v2-core` run `34539178669` がsuccess。これはCI証跡として記録するが、保護された`GATE_STATUS.json`は自己参照を避けるため書き換えていない
- 最新の`2b8f247`については、この実行環境のGitHub CLIが未認証のためActionsのexact-head結果を独立取得できていない。ローカル回帰とarchitecture checkは確認済みだが、最新CIは未確認として扱う

## Provider状態

| Provider / binding | 状態 | tier / role | 備考 |
| --- | --- | --- | --- |
| Gemini `gemini:core` / `gemini-3.8-flash` | `QUALIFIED` | L2 / core | text、ToolCall、ToolResult、multi-turn、thoughtSignature roundtrip、Controller E2E、audit、budget reconciliation。証跡: [`gemini-3.8`](../spec/v2/evidence/gemini-3.8-flash-qualification.json) |
| Gemini `gemini:worker` / `gemini-3.5-flash-lite` | `QUALIFIED` | L1 / Worker | 同上のcanonical qualification。証跡: [`gemini-3.5-Lite`](../spec/v2/evidence/gemini-3.5-flash-lite-qualification.json) |
| Gemini `gemini:compat` / `gemini-2.5-flash` | `QUALIFIED` | compatibility / verified fallback | 既存live evidenceを維持 |
| Cloudflare Workers AI / `cloudflare` | `QUALIFIED` | L1 / free cloud | canonical経路、ToolCall、audit、budget reconciliation。quotaは未報告値をunknownのまま保持 |
| OpenRouter Free / `openrouter:free` | `QUALIFIED` | L1 / late fallback | `openrouter/free`のcanonical経路、ToolCall、audit、budget reconciliation。quotaは未報告 |
| Ollama / `ollama` | `QUALIFIED` | privacy / survival | local実Provider |
| Groq | `UNQUALIFIED` | — | `/v1/models` probeがHTTP 403。permission/account状態を推測しない |
| Mistral | `UNQUALIFIED` | — | 推論HTTP 429。成功や無料枠を推測しない |
| SambaNova | `INACTIVE` | — | `/v1/models`は到達したが推論HTTP 429/402。free/no-charge qualification対象外 |
| Gemini `gemini:fast-fallback` / `gemini-3.7-flash` | `UNQUALIFIED` | L1/L2 candidate | 構成候補としてのみ文書化し、DevFarm/Routerへactivateしていない |

資格情報は環境変数または外部secret storeからのみ読み込み、repo・manifest・audit・
証跡へ値を書き込みません。Gemini固有のFunctionCall part、FunctionResponse、
thoughtSignature、thinking設定はAdapter内部で保持・変換し、Kernel protocolへ漏らしません。

## Phase 7 実行境界

- `EscalationExecutor`はControllerへ実装を追加せず、accepted review、exact plan/dispatch identity、Task state、lease、Intelligence policy、tier、capability、privacy、quota、budget、bindingを再確認してからcanonical `ProviderDispatcher`へ委譲します
- `RETRY_SAME`は同一binding、`RETRY_OTHER_PROVIDER`は同tierの別binding、`ESCALATE`はdurable allowed tier内のnext tierを選びます。`plan_id`、`dispatch_id`、`task_id`、attempt、bindingをeffect intentとdurable eventへ結合し、succeededは再送せず、dispatching/unknown/reconcilingは再実行せずreconciliationへ残します
- `IntelligenceRoutePolicy`はtierとthinking effortを別フィールドで出力します。L1はminimal、通常L2はlow、難しいL2/L3はhigh。Gemini AdapterだけがGemini 3.xの`thinkingConfig.thinkingLevel`へ変換します
- `EvaluationDispatchCoordinator`はhost evaluator結果を一回の明示review済みdispatchへ接続します。host test、最終的なTask terminal transition、次cycleのevidence生成は呼出側が所有し、自動無限retry、model自己昇格、自動mergeはありません

## Refactor Freezeの内容

- 軽量化リファクタの現行sliceでは、`src/dev_agent/operation_bootstrap.py`がprovider／qualification／resource／budgetのcomposition、`src/dev_agent/operation_planning.py`がplanning contextとchild dependency、`src/dev_agent/cli.py`がCLI parser、`src/dev_agent/state/control_repository.py`がdurable stop controlを所有する。`OperationService`は後方互換facadeとしてこれらをcompositionする
- `src/dev_agent/persistence/lease.py`がStateとSchedulerの共有lease fencing primitiveを所有し、StateからScheduler concrete implementationへのimportを除去した。`state/views.py`にはcomponent向けnarrow Protocol viewを置くが、SQLiteStateStoreのtransaction ownerは維持する
- `src/dev_agent/resources/schema.py`がResourceLedgerのschema／ordered migration、`resources/quota_store.py`がunknown-quota admission windowを所有する。ResourceLedger facade、既存store、schema version、同一SQLite transaction semanticsは維持し、内部storeへの外部直接アクセスを新たに追加していない
- `providers`／`intelligence`に加えて`resources`／`backends`／`scheduler`／`state`のpackage exportをlazy compatibility facade化し、Controllerのlegacy provider pathも互換resource-policy経路だけで構築する。`scripts/check_architecture.py`はstdlib ASTで禁止依存とinternal barrel importを検査し、`scripts/test_scope.py`は変更pathからaffected test clusterを決定する。どちらもfull regressionの代替ではない

- Controllerのprovider request実行を `runtime/model_turn.py`、compatibility direct-provider実行を `runtime/legacy_provider.py` へ分離。canonical経路は `Controller -> ProviderDispatcher` のままです
- ResourceLedgerは同一SQLite connection / lock / transaction semanticsを維持し、Catalog、Observation、Quota、Health、Budget Reservation storeを内部分離しました。schema v10でquotaのmetric／unit／window／reset／blocked state、bounded unknown-quota admission、明示Resource repair auditをordered migrationしています
- SQLiteStateStoreはconnection / transaction ownerを維持し、`state/schema.py`、`state/core_repository.py`、`state/effects_repository.py`へ内部整理しました
- ToolRuntimeは `tools/executor.py` と `tools/effect_guard.py`へ実行／副作用責務を分離し、timeout、process-tree kill、cancellation、approval、idempotency、reconciliation semanticsを維持しました
- ProviderRegistryは `providers/registry.py` を責務所有者とし、DispatcherはControlPlaneのSnapshot API経由でrouting/budget viewを取得します
- DevFarmはworktree不存在・base revision不一致・dirty状態・symlink/out-of-root・protected path・secret outbound・scope外patch・binary/submodule/symlink patch・patch上限超過をfail-closedで拒否します
- `src` と `tests/v2` の旧v1トップレベルimportは0件。v1実行資産は `legacy/v1-final` に隔離済みです

## DevFarm状態

開発WorkerはCodex/operatorが明示起動した場合だけ動作し、manifestの`external_provider_allowed`、
`approved_provider_ids`、`outbound_files`を境界にします。read可能範囲と外部送信範囲は別で、
workspace外へresolveするpath、protected/credential/secret path、secret候補を含むsourceは拒否します。
patchは実変更pathをunified diffから決定し、worktreeへだけ適用します。patch末尾LFのような
非意味的transport正規化はmetricsへ記録し、silent truncateは行いません。

外部要求の直前にも、注入されたProviderのprovider／binding／model／tierを現行のoperator activation、
capability qualification、trusted no-charge billing catalogへ照合します。資格化されていない、期限切れ、
binding不一致、課金状態不明のProviderは、manifestがprovider名を許可していても送信しません。

Proposalはrepository rootからmanifestのoutbound scopeだけを読み出すRemote stageで、Host
verification時に初めて専用worktreeを作成します。`DevFarmOrchestrator`はremote inferenceと
worktree verificationを別々のbounded governorで管理し、remoteを最大4、Hostのworktree／pytest等を
小さい枠に保ちます。remote／hostの枠を0にした場合もfail-closedです。

`gemini-worker-phase7-003` は入力ゼロの新規doc patchをhost-verifiedしました。さらに
`gemini-worker-parallel-a` / `gemini-worker-parallel-b` は独立file ownershipの2 Worker並列を
host-verifiedしました。いずれも生成物は`.devfarm/results/`（ignore対象）に保持し、smoke用の
dummy docを公式branchへ自動統合していません。実装成果の公式統合はCodexがreviewし、必要性を
確認した変更だけを行います。

Commander dogfoodでは、外部Providerへsourceを送らない決定的local harnessを使って
`phase7-commander-local-worker-004`を実行しました。`aa2f819`からのproposalを専用worktreeで
検証し、`tests/v2/test_commander_dogfood_local_004.py`の`1 passed`をHost側で確認した後、
Codexがreview・公式branchへ統合しました。Cloudflare／OpenRouter／Geminiの別試行は
proposal品質または応答失敗でHost Verifiedに至っておらず、外部Free Worker成功とは扱っていません。

## 監査再分類と次の作業

1. Commander dogfood、CloudflareのOperation external E2E、L1 alternate→L2 hierarchy、quota reset→bounded probe→domain-scoped wakeは完了。未提供quota値は引き続きunknownのまま扱う
2. Worker metricsとhard-filter限定のevidence advisoryは実装済みだが、sample／freshness／rollback条件が揃うまでResourceRouterへhard接続しない
3. ローカルで閉じたOperation／billing／quota／cancellation／Commander境界は回帰済み。GitHub rulesetによるrequired check、OS級filesystem/network sandbox、実paid Provider資格化はdeployment／外部条件として未完了であり、コード完了とは扱わない
4. 実Codex AgentBackend adapter／MCPは、このhardening判定後の専用Gateで開始する。G6O1は実paid Providerとdeployment-owned budget configuration待ちのまま、Phase 7コード判定と混ぜない

G6O1は実paid Providerとdeployment-owned budget configurationという外部条件待ちであり、
コード不足として勝手に昇格しません。`README.md`は入口、`PHASE6_PLAN.md`はPhase 6受入条件、
`V2_EXECUTION_PLAN.md`はロードマップ、`CHANGELOG.md`は履歴、`TRACEABILITY.md`は要求と実装所有者の
追跡に限定します。
