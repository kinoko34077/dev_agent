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

## Current activation boundary

The farm is scaffolded and contract-tested, but no free cloud Worker is
activated until a real Provider qualification succeeds. In this workspace
Groq and Cloudflare credentials were absent, so both live qualification probes
remain `blocked_external`. No live or Gate evidence is inferred from adapter
unit tests.
