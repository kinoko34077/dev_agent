# Provider failure playbook

This document records observed failures and the condition required to retry.

## Local Ollama qualification — 2026-09-08 JST

- Observation: `GET /api/tags` succeeded and the installed `qwen3:8b` completed the Controller Tool-call roundtrip.
- Qualification: `model.requested -> model-generated ToolCall -> tool.completed -> final model response -> task.completed` passed with a bounded output request.
- Known quirk: `qwen3:8b` may emit a `<think>` trace; this is recorded as output-quality metadata, not as a transport failure. The smaller `qwen3:0.6b` can spend a narrow output bound on reasoning text and is not the selected qualification model.
- Safe retry: confirm `GET /api/tags`, select a recorded model, use `stream: false`, enforce the output bound, and require visible response quality.

The adapter uses `POST /api/chat`, `stream: false`, and converts the response `message.content` / `message.tool_calls` into the internal protocol. Tool results are sent with the call ID, status, and result data preserved.

### Output-budget propagation failure — detected during live probe

- Observation: a request whose internal `max_output_tokens` was 16 produced an Ollama response reporting `eval_count: 336`.
- Cause: the first adapter version did not translate the provider-neutral bound into Ollama's runtime `options.num_predict`; Ollama therefore used its model default.
- Correction: every `/api/chat` request now includes `options.num_predict = ModelRequest.max_output_tokens`, with a unit assertion for that mapping and a repeatable live check against the returned `eval_count`.
- Safe retry condition: use an already-installed model, request a small bound, and fail the probe if `eval_count` exceeds the requested bound. Do not infer a bound from prompt wording alone.

### Reasoning-token exhaustion at a small bound — detected during live probe

- Observation: after enforcing a 16-token bound, the reasoning-capable local model stopped at the bound while emitting only its thinking trace rather than the requested short answer.
- Cause: the same output budget is shared by model reasoning and visible response content.
- Failed mitigation: the documented `think: false` request was accepted by the installed Ollama 0.7.0 service for `qwen3:0.6b`, but the model still emitted a thinking trace. It is therefore not a portable control and is deliberately not baked into the generic adapter.
- Safe retry condition: qualify each selected model with both a bounded `eval_count` and an expected visible response for a concise prompt. Choose a model/version that passes this check, or introduce an explicit protocol-level reasoning budget and a provider/version-specific adapter; do not assume a request flag works merely because it receives HTTP 200.

## Gemini live qualification — 2026-09-08 JST

- Observation: `gemini-2.5-flash` completed live text, model-generated ToolCall, ToolResult, and final response roundtrip.
- Qualification: the normalized result and capability matrix entry are the Phase 5 evidence; credentials remain environment-only and are not written to events.

The v2 `GeminiHttpProvider` now reads `GEMINI_API_KEY` from the process environment at request time, sends it via the documented `x-goog-api-key` header, sends `generationConfig.maxOutputTokens`, and decodes the documented `generateContent` response. It fails closed when the variable is absent; a `.env` file is not loaded implicitly.

### Gemini live probe — network reached, HTTP 403

- Observation: with the key present and network permission granted, the minimal `gemini-2.5-flash` request reached Google and returned HTTP 403.
- Meaning: this is no longer a local transport failure. The key may be invalid, restricted from the Generative Language API, attached to a project where the API is disabled, or not permitted to use the selected model.
- Safe remedy: in Google AI Studio / Cloud, verify the key belongs to the intended project, enable the Generative Language API, review application/API restrictions, and confirm the model is available to that key. Then repeat the same minimal probe; never paste the key into source, chat, or logs.
- Current gate: the live contract is verified; future 401/403 observations remain authentication/authorization blockers and are not capability passes. The adapter does not retry them automatically.
- Follow-up: switching from URL query authentication to the official `x-goog-api-key` header still returned HTTP 403 for both the model-list request and the minimal completion. This rules out the original query-vs-header choice as the sole cause; credential/project restrictions remain the active blocker.

## Failure classification

- Connection refused / DNS / timeout: `transport`.
- HTTP authentication / quota / 429: adapter must classify into `authentication`, `quota`, or `rate_limit`.
- Unparseable provider response: `provider_decode`.
- Tool result that cannot be correlated to its call: `schema_validation`.
