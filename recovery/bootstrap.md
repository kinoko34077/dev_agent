# Recovery skeleton

The recovery path is deliberately independent from the normal v2 runtime and
uses only the Python standard library. From the repository root:

```bash
python recovery/diagnose.py
python recovery/diagnose.py --json
python recovery/validate_state.py path/to/task-state.json
```

These commands are read-only. They do not contact a Provider, import the v1
runtime, modify Git refs, or alter configuration. Future repair operations must
remain explicit and separately permissioned.
