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
output contract. `scripts.devfarm.validate_manifest()` rejects path escapes,
overlapping ownership, and attempts to own the built-in protected files
(`spec/v2/GATE_STATUS.json`, budget authority) or the `recovery/` and
`.devfarm/` trees. A result must use the same base revision and may list only
files from `allowed_files`; a Worker cannot modify `v2/bootstrap`, Gate status,
budget authority, Recovery policy, credentials, or another Worker's worktree
by convention and contract.

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

## Provider qualification handoff

Live qualification is an operator-invoked boundary and never runs in CI. Keep
credentials in the process environment or an external secret store; do not
put them in a manifest, command argument, repository file, or result artifact.

```text
python scripts/qualify_free_provider.py --provider groq --model <groq-model-id> --evidence-path .devfarm/results/groq-live.json
python scripts/qualify_free_provider.py --provider cloudflare --model <cloudflare-model-id> --evidence-path .devfarm/results/cloudflare-live.json
python scripts/qualify_free_provider.py --provider sambanova --model Meta-Llama-3.3-70B-Instruct --evidence-path .devfarm/results/sambanova-live.json
```

The Groq probe uses `GROQ_API_KEY`; the Cloudflare probe uses
`CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN`; the SambaNova probe uses
`SAMBANOVA_API_KEY`. Missing credentials are reported as `blocked_external`.
SambaNova is included in this opt-in no-charge qualification path because the
roadmap treats it as a free-tier candidate, but the script does not prove
billing tier or paid worst-case cost. A successful result is still a
qualification artifact for Codex review, not automatic Gate promotion or
Worker activation.

## Current activation boundary

The farm is scaffolded and contract-tested, but no free cloud Worker is
activated until a real Provider qualification succeeds. In this workspace,
Cloudflare has a successful live qualification artifact, while Groq returned
HTTP 403 and SambaNova returned HTTP 429 after reaching the API. Therefore no
Worker is activated yet. No live or Gate evidence is inferred from adapter
unit tests.
