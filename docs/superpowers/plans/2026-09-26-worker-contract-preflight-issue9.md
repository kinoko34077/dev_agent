# Issue #9 — Worker contract and deterministic preflight

## Scope

Close the next roadmap slice after the Issue #5/Phase 8 composition proof:
make the existing bounded full-file replacement path an explicit Worker output
mode and record deterministic Host preflight outcomes before any refinement or
provider reassignment decision.

## Ownership and constraints

- Codex-owned: the cross-cutting Worker contract/preflight composition and
  affected regression tests. A narrow Worker is not used because this slice
  changes the shared dispatch contract and its failure projection.
- Reuse the existing manifest, egress, patch validator, Host Verification,
  refinement, and provider admission boundaries.
- No new scheduler, queue, state store, provider registry, retry engine, or
  authority.
- Provider/runtime failures remain outside model-output repair. UNKNOWN and
  reconciliation outcomes must not receive a preflight repair classification.
- The Host may canonicalize only transport-level replacement formatting; it
  must never invent source content or paths.

## Implementation steps

1. Add a bounded manifest-selectable `minimal_file_replacement` output mode,
   retaining the historical full-result mode by default.
2. Reuse the existing Host materializer for complete text/line-array proposals
   and expose a bounded deterministic preflight projection with error code,
   stage, fallback disposition, and canonicalization audit.
3. Use the same preflight path for local Ollama and explicitly opted-in remote
   Worker manifests; keep Host Verification as the acceptance boundary.
4. Add focused tests for valid canonicalization, fail-closed scope/schema
   rejection, fallback disposition, provider-failure separation, and legacy
   full-result compatibility.
5. Run only affected tests first; expand to the shared DevFarm regression if a
   changed boundary requires it. At slice completion run compile, secret scan,
   commit/push, and exact-head CI evidence.

## Acceptance

- A valid minimal proposal becomes a Host-generated patch and remains subject
  to existing Host Verification.
- Unknown minimal-contract fields, malformed JSON, invalid paths, embedded
  line breaks, unsafe/secret content, and invalid patches fail closed with
  bounded structured preflight metadata.
- Deterministic proposal rejection is marked for bounded correction before
  model fallback; provider transport/reconciliation has no model preflight.
- Existing full-result Worker manifests and provider failures retain their
  current behavior.
- No Gate value changes; live remote availability and Discord live E2E remain
  truthful external evidence rather than being inferred from local tests.
