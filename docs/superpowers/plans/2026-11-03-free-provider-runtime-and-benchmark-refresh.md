# Free Provider runtime wiring and benchmark refresh

**Owner:** dev_agent #81. **Implementation reason:** `cross_cutting` (candidate materialization, exact billing lookup, Operation/DevFarm/Host dispatch handoff, and benchmark evidence).

## Goal

Make currently configured, exact no-charge remote model routes reachable by the existing Operation/DevFarm execution paths, refresh external benchmark observations, and prove one bounded free route end to end. Preserve exact billing, capability, quota, privacy, Host verification, and formal Gate boundaries. Do not dispatch Claude or DeepSeek.

## Baseline and observed gap

- `v2/bootstrap@800db6c8af6dc2736b46358b75ce3f7dcd81729c`; local branch was clean and remote `v2/bootstrap` matched at start.
- Current configured Operation pool auto-activates from available provider credentials when no single-route override is set; it contains 14 bindings in this environment.
- Offline replay of the staged 2026-11-02 inventory shows OpenRouter `:free` routes miss billing because runtime uses `openrouter:free` while the dynamic free billing rule only matches `openrouter:account`.
- The shared 128-candidate cap is applied before exact billing priority; all four current exact no-charge Vercel candidates are dropped from its sorted prefix.
- Canonical benchmark snapshot remains expired even though candidate model discovery was refreshed. External benchmark evidence is advisory quality data, not runtime billing/admission authority.

## Work slices

1. **Failing regression first:** add exact tests for OpenRouter qualification-lane free billing and retaining current no-charge Vercel models within the bounded candidate set. Run only these tests and confirm the failures.
2. **Runtime wiring fix:** support the existing OpenRouter free qualification lane without broadening to non-`:free` IDs; allow shared materialization callers to rank exact current no-charge candidates before the remaining deterministic bounded prefix while preserving the configured seed. Wire the same callback through Operation bootstrap, DevFarm resource-pool admission, and Host dispatch identity resolution.
3. **Focused route checks:** rerun the two regressions and existing Operation/DevFarm resource-pool/Host-dispatch tests that exercise these seams. Inspect the actual composed candidate set using secret-free identity output.
4. **Benchmark evidence:** use the existing read-only OpenRouter Benchmarks API adapter against the latest staged catalog; inspect exact identity matches, source/version, timestamp, expiry, and task-fit fields. Promote only valid exact current rows to the canonical benchmark snapshot. Missing scores remain a warning and never remove a current no-charge proposal route.
5. **Runtime proof:** submit one bounded text-only request through an existing exact no-charge route with a fresh identity and strict small output. Verify provider/model identity, response, cost/billing metadata, and no replay. If the attempt yields UNKNOWN or provider failure, preserve it and do not retry that identity.
6. **Durable record:** update #81 with route diagnosis, benchmark source/result, exact runtime attempt outcome, and remaining evidence gaps. Formal #65/#11 state stays unchanged.

## Risks and controls

- A model-list row is not a billing grant. Only the current exact trusted no-charge profile can receive priority or pass admission; unknown/paid routes remain blocked.
- OpenRouter free suffix recognition applies only to the existing account/free qualification lanes and model IDs ending in `:free`; exact `openrouter/free` remains separately configured.
- Candidate ranking changes bounded ordering only; it does not alter capability, qualification, quota, privacy, or Host acceptance.
- Benchmark scores are external quality observations, not evidence of task success or formal qualification.
- No credentials, payment settings, protected Gate, `.devfarm`, unknown-effect replay, or paid Provider route are changed.

## Verification / completion

- Focused regression tests for both reproduced defects and affected runtime composition seams.
- No broad suite repetition unless a focused failure or cross-cutting change requires it; required exact-head CI remains a delivery acceptance condition.
- One actual bounded no-charge request through the standard runtime path, or a precisely classified terminal blocker with no unsafe retry.
- Record local/remote commit relationship and leave the worktree clean after delivery.

## 2026-11-04 checkpoint

- Runtime materialization, Operation, DevFarm resource-pool, Host dispatch, and
  reviewer/planner paths now prioritize exact current no-charge candidates
  before the bounded 128-row cap. The configured seed remains first; every
  route still passes exact current billing, text capability, quota, privacy,
  health, and Host validation. Missing benchmark/qualification is restricted
  to low-risk L1 proposal-only work; formal L2 and the Phase 8 root contract
  remain unchanged.
- `OperationConfig.from_environment()` now loads the reviewed repository model
  evidence by default. The current configured pool has 14 bindings across 9
  providers. Current exact no-charge proposal inventory: 84 binding/model rows
  plus 6 stale advisories; for the configured pool, 76 unique current remote
  identities are available across Cloudflare (29), Gemini (4), Mistral (27),
  OpenRouter (13), and Vercel (3). Nine exact identities have an external
  coding/planning benchmark alias; the other 67 remain warning-level rather
  than benchmark-blocked.
- Reviewed external benchmark evidence was refreshed through the existing
  OpenRouter Benchmarks API adapter. Current repository snapshot: 101 rows, 38
  current; 19 current rows include coding or planning task-fit scores. Model
  identity aliases are exact reviewed mappings, never fuzzy similarity.
- A bounded real OpenRouter `:free` request completed once through the existing
  Operation route at zero cost, with one provider call, no fallback, and no
  reconciliation requirement. Sanitized evidence is in
  `spec/v2/evidence/free-provider-operation-smoke-20261104.json`. This proves
  only that exact L1 proposal route, not Phase 8 or other routes.
- Full `tests/v2` was run once and found 9 stale expectations among 2300 passes
  and 1 skip. The 9 affected cases were corrected to isolate qualification,
  current billing ambiguity, and the new Worker metadata, then all 9 passed.
  The earlier affected focused suite passed 301 tests. Architecture,
  compileall, semantic coherence/model funnel, evidence-expiry preflight, and
  secret-pattern scan pass. Exact-head CI is still required before integration.
- Formal `#65` remains PRECHECK_BLOCKED, formal `#11` remains
  `PHASE8_LIVE_ACTIVATION=NOT_VERIFIED`; no Claude/DeepSeek paid route,
  credential mutation, Gate change, or unknown-operation replay was used.
