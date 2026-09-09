# Development Worker Farm

`.devfarm/` is a development-only handoff area. It is not the v2 runtime
scheduler, a Phase 7 AgentBackend, or a source of Gate evidence. It is ignored
by Git and is created with:

```text
python scripts/devfarm.py init
```

The intended ownership is:

```text
Codex (Commander / integrator)
  ├─ .devfarm/tasks/<task-id>.json
  ├─ .devfarm/results/<task-id>/result.json
  └─ .devfarm/worktrees/<task-id>/   # one worker checkout
```

Each worker receives a manifest with a fixed `base_revision`, `allowed_files`,
`read_files`, `forbidden_files`, finite attempts, acceptance checks, and an
output contract. External sending is a separate explicit boundary:
`external_provider_allowed`, `approved_provider_ids`, and `outbound_files`.
Only `outbound_files` are read into the Provider request, and they must be a
subset of the manifest's readable scope. `scripts.devfarm.validate_manifest()`
rejects path escapes, overlapping ownership, unsafe test commands, and attempts
to own or send built-in protected files (`spec/v2/GATE_STATUS.json`, budget
authority, `.env*`, credentials, private/token stores, `recovery/`, and
`.devfarm/`).

Before a request, the Runner requires the task worktree to exist, have a clean
status, and resolve to the manifest's exact Git base revision. Input paths are
resolved and symlink escapes are rejected; outbound contents are scanned for
common secret patterns and fail closed. The result's `changed_files` is derived
from the unified patch, not trusted from the model claim. A result must use the
same base revision and may list only files from `allowed_files`; a Worker cannot
modify `v2/bootstrap`, Gate status, budget authority, Recovery policy,
credentials, or another Worker's worktree by convention and contract.

Prepare a separate checkout with an `agent/<provider>/<task>` branch:

```text
python scripts/devfarm.py prepare-worktree groq-quota-001 agent/groq/task-001 --revision <base-revision>
```

The command delegates only worktree creation to Git. Worker execution,
provider selection, review, cherry-pick, conflict resolution, official commit,
Gate promotion, and Current State synchronization remain Codex responsibilities.
Workers do not communicate directly; dependent work is passed through the
result artifact and then a new manifest.

Codex can validate handoff artifacts before review or integration:

```text
python scripts/devfarm.py validate-manifest .devfarm/tasks/<task-id>.json
python scripts/devfarm.py validate-result .devfarm/results/<task-id>/result.json --manifest .devfarm/tasks/<task-id>.json
```

Both commands print the normalized contract and fail closed on malformed JSON,
base-revision drift, protected ownership, or an out-of-scope changed file.

The bounded worker runner can send the explicitly approved manifest-scoped
outbound files to an approved qualified free Provider and writes only handoff
artifacts. Automatic activation, unapproved sending, automatic patch
application, commits, Gate promotion, and official-branch modification are not
performed. Review the outbound input scope before invoking it:

```text
python scripts/devfarm_worker.py \
  --manifest .devfarm/tasks/<task-id>.json \
  --provider cloudflare \
  --model @cf/meta/llama-3.1-8b-instruct
```

Gemini 3.5 Flash-Lite is also an explicitly activated L1 Worker model after
its live qualification. The Runner binds it to `gemini:worker`; other Gemini
models are not implicitly activated for DevFarm work:

```text
python scripts/devfarm_worker.py \
  --manifest .devfarm/tasks/<task-id>.json \
  --provider gemini \
  --model gemini-3.5-flash-lite
```

After Codex reviews the proposal, deterministic validation and host-side test
execution are explicit and restricted to the same worker worktree:

```text
python scripts/devfarm_worker.py \
  --manifest .devfarm/tasks/<task-id>.json \
  --apply-and-verify
```

This applies the validated patch only in `.devfarm/worktrees/<task-id>/` and
runs only the manifest-approved `python -m pytest` / `python -m compileall`
commands. `result.json` records `model_claims`, `proposed_test_commands`, and
`host_verified_tests` separately. The runner is a development bootstrap
boundary, not the formal Phase 7 AgentBackend. It also records host-side
`worker_metrics` (provider, binding, model, tier, request id, elapsed time,
safe usage scalars, attempt count, and acceptance). Model-reported test claims
are never copied into the verified result.

## Provider qualification handoff

Live qualification is an operator-invoked boundary and never runs in CI. Keep
credentials in the process environment or an external secret store; do not
put them in a manifest, command argument, repository file, or result artifact.

```text
python scripts/qualify_free_provider.py --provider groq --model <groq-model-id> --evidence-path .devfarm/results/groq-live.json
python scripts/qualify_free_provider.py --provider cloudflare --model <cloudflare-model-id> --evidence-path .devfarm/results/cloudflare-live.json
python scripts/qualify_free_provider.py --provider openrouter --model openrouter/free --evidence-path .devfarm/results/openrouter-live.json
python scripts/qualify_free_provider.py --provider gemini --model gemini-3.5-flash-lite --evidence-path .devfarm/results/gemini-live.json
python scripts/qualify_free_provider.py --provider mistral --model <mistral-model-id> --evidence-path .devfarm/results/mistral-live.json
```

The Groq probe uses `GROQ_API_KEY`; the Cloudflare probe uses
`CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN`. Mistral uses
`MISTRAL_API_KEY`; its latest configured-key attempt reached the API but
returned HTTP 429 and remains unqualified. Missing credentials are reported
as `blocked_external`. SambaNova has a separate HTTP Adapter, but is not
included in this no-charge qualification command until its billing tier and
worst-case cost are explicitly qualified. A successful result is still a
qualification artifact for Codex review, not automatic Gate promotion or
Worker activation.

## Current activation boundary

The farm's isolation and host-verification boundary is implemented and
contract-tested, but no Worker is activated automatically: activation requires
an explicit Codex/operator launch, an approved manifest, and review. In this
workspace, Cloudflare, OpenRouter, and Gemini 3.5 Flash-Lite have successful
qualification artifacts. Gemini task `gemini-worker-phase7-003` produced a
valid patch and passed its manifest-approved host test (`7 passed`). The
independent tasks `gemini-worker-parallel-a` and
`gemini-worker-parallel-b` were run concurrently in separate worktrees and
both passed host verification (`7 passed` each). Their ignored result
artifacts remain under `.devfarm/results/`; they are not official branch
changes or Gate evidence. Groq returned HTTP 403, SambaNova returned HTTP
429/402, and Mistral returned HTTP 429; none is activated as a Worker.
No live or Gate evidence is inferred from adapter unit tests, and a Worker
result does not become an official change until Codex reviews and integrates
it.
