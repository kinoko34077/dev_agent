# ADR: Host Verification trust levels

Status: Accepted for the current hardening slice

## Decision

Development Worker verification has three explicit levels:

- `STATIC_ONLY`: validate the patch, paths, Git applicability, and immutable
  evidence. Do not execute patched code.
- `TRUSTED_HOST_EXEC`: execute the allowlisted test specification only after an
  operator approves that specific attempt. This is contained host execution,
  not an OS sandbox.
- `OS_SANDBOXED`: reserved for a future implementation that provides
  filesystem isolation, network deny-by-default, resource limits, and process
  containment. It is not advertised as available until those controls exist.

External-provider manifests default to `STATIC_ONLY`. A proposal is never an
accepted Worker result merely because the model claims tests passed. An
executable verification must also include a trusted target outside the files
changed by the Worker; Worker-owned test changes alone are insufficient.

The current Host Verification runner sanitizes credentials from its
environment, uses a temporary home, bounds output, and terminates the process
tree on timeout. It records containment limitations explicitly and does not
claim OS filesystem or network sandboxing.

## Consequences

Unattended external Worker code execution is disabled until a real
`OS_SANDBOXED` runner is implemented. An operator may approve a narrow
`TRUSTED_HOST_EXEC` attempt for review, but that approval does not weaken
protected paths, budget, qualification, or integration proof.

Host test commands use a strict structured subset: `pytest` or `compileall`,
relative in-worktree targets, and only bounded `-q`, `-x`, or `--maxfail=N`
options. Plugin/config/output-path options are rejected.
