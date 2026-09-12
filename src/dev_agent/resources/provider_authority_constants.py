"""Stdlib-only authority constants for provider locality classification.

Zero dev_agent imports — recovery scripts can import this without pulling
in the full runtime stack.  All authority modules (provider_policy, repair,
router, recovery/validate_resources) derive their local/remote decision from
here, not from independent inline sets.
"""

from __future__ import annotations

# Providers whose execution path is entirely operator-controlled with no
# external network endpoint.  New providers default to remote (cloud) unless
# explicitly added here by an authority owner.
LOCAL_PROVIDER_IDS: frozenset[str] = frozenset({"ollama", "fake"})

# Subset that represent real local hardware (not test stubs).
REAL_LOCAL_PROVIDER_IDS: frozenset[str] = frozenset({"ollama"})
