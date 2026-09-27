# RUNTIME_ELIGIBLE blocker audit — 2026-09-27

Scope: why `RUNTIME_ELIGIBLE=0` persists for the Phase 8 formal remote route (#11),
plus a bounded #24 exposure classification. Read-only audit; no network, generation,
credential, billing, quota, or Gate mutation. Base: `v2/bootstrap` @ `bdd0608`
(moved from `83a81ca`; the only new commit is a docs sync).

## A. Structural blockers (code-level, reproducible)

### A1. Gemini adapter never emits quota telemetry → numeric-quota ELIGIBLE is unreachable

- `providers/gemini/provider.py` sets no `usage["quota_observation"]`.
- `openai_compatible/http.py::_quota_observation` returns `None`; OpenRouter inherits it.
- Only Cloudflare (neurons), Groq, and SambaNova emit quota observations.
- `QuotaRequalificationCoordinator.probe_once` requires positive routable headroom and
  only runs for already-blocked observations.

Consequence: #11 §6 step 2 ("obtain an existing runtime-owned quota observation") has
no producer for any Gemini candidate. Waiting for one will not converge.

### A2. Admission evaluator is stricter than the real dispatch path

- `RuntimeAdmissionEvaluator.evaluate` builds `RouteRequest(capabilities={"text"}, ...)`
  with the default `allow_unknown_quota=False`.
- `operation_bootstrap` builds the real Controller with
  `allow_unknown_quota=trusted_free_binding_present`, so production routing admits a
  trusted no-charge (`hard_stop`) route under the bounded UNKNOWN-quota bootstrap.

Reproduction (temporary ledger, `OperationService._ensure_resource` for
`gemini:worker:free-3 / gemini-3.5-flash-lite`, real `QualificationResolver`):

| path | result |
|---|---|
| `RuntimeAdmissionEvaluator.evaluate` | `RUNTIME_UNKNOWN` |
| `ResourceRouter.choose(allow_unknown_quota=False)` | `NoRoute` |
| `ResourceRouter.choose(allow_unknown_quota=True)` | selects `gemini:worker:free-3`, `unknown_quota=True` |

So "diagnostic says UNKNOWN" and "runtime will dispatch" are simultaneously true.
A1 + A2 together make `RUNTIME_ELIGIBLE` for Gemini impossible by construction.

Decision needed (Human / spec, not auto-fixed here — it changes admission semantics):

1. Add a distinct evaluator status (e.g. `RUNTIME_BOOTSTRAP_ADMITTED`) that mirrors the
   router's trusted no-charge UNKNOWN bootstrap, and decide whether formal R9 may use it; or
2. keep ELIGIBLE numeric-only and move the formal route to a provider that emits quota
   telemetry (Cloudflare is configured, qualified, and trusted-catalog), or
3. implement Gemini quota evidence from a real source (none exists in the response
   headers today; do not fabricate).

### A3. Candidate scope excludes the only telemetry-capable configured routes

The configured pool has `cloudflare` and `openrouter:free`, both qualified and in the
trusted billing catalog, but the evidence `candidate_scope` contains only four Gemini
identities. Cloudflare is the one configured remote route whose adapter can produce a
numeric quota observation; it is never evaluated.

### A4. Pool composition lacks 3.6/3.8 resources

`_ensure_resource` registers one resource per binding (`binding.model`), so the three
3.6/3.8 candidates are `RUNTIME_UNAVAILABLE` because the operator env does not bind
them, not because the Provider failed. Operator config change, as #11 already notes.

## B. Time-bound risks (will independently force UNAVAILABLE/UNKNOWN soon)

| item | expires |
|---|---|
| billing catalog default `_DEFAULT_EXPIRES_AT` (all trusted profiles) | 2026-10-09T00:00Z |
| qualification `gemini:compat / 2.5-flash` | 2026-10-08 (JST) |
| qualification `ollama / qwen3:8b` | 2026-10-08 (JST) |
| qualification `cloudflare`, `openrouter:free` | 2026-10-09 (JST) |
| qualification `gemini:worker/core` 3.5-lite / 3.8 | 2026-10-09 |
| qualification `free-3/4/5` 3.6/3.8 | 2026-10-13 |
| qualification `free-3 / 3.5-flash-lite` | 2026-10-15 |

After 2026-10-09 the router rejects every `trusted_catalog` resource
(`billing_expires_at` check), so even a solved A1/A2 fails closed. Re-verification of
the billing catalog must precede or accompany any formal R9 attempt.

### B2. Stale resource observation on reused ledgers

`RouteRequest.max_observation_age_seconds=300`, and `_ensure_resource` returns early for
existing rows. A persistent ledger reopened >5 min after registration yields
`RUNTIME_UNKNOWN` regardless of quota. The recorded configured-pool evidence used a
fresh temporary ledger, so it did not hit this, but a formal run on a persistent ledger
will.

## C. #24 bounded exposure classification (legacy `main`, counts only)

`origin/main` tracks 180 files (~654 KiB) under `logs/` (74) and `memory/`
(`context` 7, `inputs` 61, `outputs` 31, `summaries` 1, root 6). `.gitignore` covers
only venv / pycache / `.env`.

| category (current tree) | files |
|---|---|
| Google / OpenAI / OpenRouter / GitHub key patterns | 0 |
| key/secret/password assignment | 0 |
| email address | 0 |
| local user path | 0 |
| conversation-shaped material | 1 |
| raw provider response shape | 21 |

History scan (all refs): no real-key pattern; the single `sk-…` hit is the `sk-test…`
fixture in `tests/v2/test_devfarm_patch_validation.py`. Local-path patterns appear in
10 commits (not classified further here).

Assessment: no credential exposure detected by pattern scan, so no rotation trigger.
Remaining exposure is prompt/response/conversation content. Reversible next step
(branch/PR on `main`): add `logs/` and `memory/{inputs,outputs,context,summaries}/`
to `.gitignore` and untrack them. History rewrite and default-branch change still need
explicit Human confirmation per #24.
