# Work Address and Host Egress Requirements

## Work address

- `task_id` remains the immutable machine identity; `work_address` is an optional
  human-readable position and must not replace UUID references.
- An address contains one or more `positive_integer` or `uppercase_letter`
  segments separated by `-`; mixed nesting such as `5-B-8-3` is valid.
- Numeric segments express sequential position and letter segments express a
  parallel lane. Dependencies and ownership, not the address alone, control
  readiness and concurrency.
- A bounded resume capsule records the active address, checkpoint, next action,
  owned paths, and artifact references without raw conversation.
- The capsule may carry the existing Task UUID for durable correlation while
  remaining readable when that optional field is absent. It can project the
  parent return point into a bounded frame, push a suspended projection, and
  pop the latest frame; full parent state remains owned by the existing
  Task/checkpoint store.
- NOTE, PARALLEL, INTERRUPT, and explicit CANCEL are distinct. Ambiguous input
  must not interrupt active work. Interrupts use a bounded LIFO return stack and
  safe checkpoints.

## Host egress

- A standing low-risk source grant and a per-dispatch content manifest are
  separate records. The grant does not authorize arbitrary bytes or destinations.
- Host checks path containment, symlink escape, sensitivity, deny patterns,
  secret-shaped content, size, destination, and existing approval/privacy policy
  before external send.
- Only an `ALLOW` manifest reaches an external Worker. `REVIEW` and `DENY` are
  returned as structured outcomes; lower models do not re-decide the Host gate.
- Control, authority, approval, and security conditions are never part of the
  compressible or externally supplied Payload.
- Egress metadata is bounded and secret-free. Provider/transport/policy/content
  failures remain distinguishable, and UNKNOWN external effects are reconciled
  rather than blindly retried.

This requirement extends the existing Commander/Coordination and DevFarm
boundaries; it does not create a second scheduler, approval system, retry engine,
or process authority.
