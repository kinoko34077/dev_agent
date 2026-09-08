# ADR-011: Gemini API adapter strategy

Status: accepted for Phase 3.5

The Kernel remains provider-neutral. Gemini integration uses adapters: the
existing `generateContent` adapter is compatibility-only and a future
Interactions adapter is primary. Both must pass the same ContractHarness case
matrix and record model, adapter version, tested_at, capabilities, and typed
failures. A Gemini HTTP 403 is an authorization blocker, never a capability pass.
