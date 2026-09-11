# Multi-provider bindings and billing modes

## Goal

Add the newly configured Gemini key slots plus Ollama Cloud and Vercel AI
Gateway through the existing ProviderFactory, Registry, Operation pool,
qualification, resource, and billing boundaries. Keep local Ollama distinct
from Ollama Cloud, and never represent allowance-backed services as a
zero-cost fixed-free resource.

## Constraints

- Preserve existing ProviderDispatcher, ResourceControlPlane, qualification,
  quota, budget, and Operation composition boundaries.
- Do not persist or print API-key values.
- Provider presence or an API key is not qualification evidence.
- New remote bindings remain inactive for production routing until their exact
  provider/binding/model qualification and billing authority are current.
- Use the existing OpenAI-compatible transport only where the provider wire
  contract is actually compatible; keep local Ollama identity separate.

## Implementation slices

1. Audit current billing profiles, provider definitions, Operation pool loading,
   qualification identity, and existing adapters. Add focused red tests for
   billing modes and the three new provider identities.
2. Extend `TrustedResourceProfile` with validated billing mode and allowance
   metadata. Keep fixed-free, allowance-backed, credit-backed, paid, and
   unknown semantics explicit, with focused catalog tests.
3. Add Gemini key-slot bindings with project/quota-domain metadata without
   embedding credentials. Preserve the existing Gemini compatibility and
   qualified bindings.
4. Add Ollama Cloud and Vercel AI Gateway definitions through the existing
   OpenAI-compatible provider boundary, with explicit endpoint, environment
   key, binding, quota, and billing metadata. Do not register local Ollama as
   a remote provider.
5. Extend Operation provider-pool construction from environment/configuration
   so configured bindings can be used without replacing the explicit manual
   pool/debug override. Missing model configuration remains fail-closed rather
   than inventing a qualified model.
6. Add resolver/qualification and resource projection tests proving that
   configured-but-unqualified bindings are not silently routed, while exact
   qualified entries can be admitted through the canonical path.
7. Run focused provider/catalog/operation tests, full `tests/v2`, architecture
   and gate checks, then update Current State, capability evidence, traceability,
   and changelog. Commit and push only after the verified slice is coherent.

## Verification

- API-key values are never included in source, artifacts, logs, or docs.
- Existing local Ollama tests remain local (`provider_id=ollama`).
- `ollama_cloud` and `vercel` have distinct identities and billing modes.
- Gemini slot bindings are distinct by binding/quota domain and do not change
  the existing exact-identity qualification rules.
- No new scheduler, state store, router, or agent framework is introduced.
- Full regression remains the final gate.
