# Recovery skeleton

The recovery path is deliberately independent from the normal v2 runtime and
uses only the Python standard library. From the repository root:

```bash
python recovery/diagnose.py
python recovery/diagnose.py --json
python recovery/validate_state.py path/to/task-state.json
python recovery/validate_sqlite_state.py path/to/runtime.sqlite3
python recovery/test_results.py path/to/junit.xml --json
```

`recovery.git_recovery` provides read-only `inspect_git`,
`record_last_known_good`, and `plan_rollback` helpers.  `restore_sqlite` also
requires an explicit `replace=True` before overwriting an existing destination.
Rollback and repair-branch mutation require `allow_destructive=True` or
`allow_write=True` respectively; they are not part of the diagnostic commands.
Rollback also refuses a dirty worktree unless the caller separately passes
`allow_dirty=True`, making the loss of uncommitted changes an explicit choice.

The diagnostic commands do not contact a Provider, import the v1 runtime,
modify Git refs, or alter configuration. Destructive recovery remains an
explicit operator action.

When a CI JUnit artifact is available, pass it to
`python recovery/diagnose.py --test-report path/to/junit.xml` to include a
read-only persisted-test-report check in the diagnostic output.
