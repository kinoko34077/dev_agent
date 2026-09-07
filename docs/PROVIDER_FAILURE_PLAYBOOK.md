# Provider failure playbook

This document records observed failures and the condition required to retry.

## Local Ollama probe — 2026-09-08 JST

- Observation: `ollama.exe` is installed, but `http://127.0.0.1:11434/api/tags` returned connection refused.
- Cause: the local Ollama server is not running or is not listening on the default local API port.
- Safe retry: start the local server, confirm `GET /api/tags` succeeds, choose an already-installed model, then run the local Provider probe with `stream: false`.
- Do not treat an installed executable as a usable inference resource.

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

## Gemini live probe — 2026-09-08 JST

- Observation: `GEMINI_API_KEY` is not configured in the current process.
- Cause: no credential is available to perform an authenticated probe.
- Safe retry: provide the key through the environment only, run the probe against a selected model with the smallest bounded request, record model/version/time/result, and redact raw credentials from all events.
- Until then, the Gemini adapter is verified against sanitized REST response fixtures only.

The v2 `GeminiHttpProvider` now reads `GEMINI_API_KEY` from the process environment at request time, sends `generationConfig.maxOutputTokens`, and decodes the documented `generateContent` response. It fails closed when the variable is absent; a `.env` file is not loaded implicitly.

### Gemini live probe — network reached, HTTP 403

- Observation: with the key present and network permission granted, the minimal `gemini-2.5-flash` request reached Google and returned HTTP 403.
- Meaning: this is no longer a local transport failure. The key may be invalid, restricted from the Generative Language API, attached to a project where the API is disabled, or not permitted to use the selected model.
- Safe remedy: in Google AI Studio / Cloud, verify the key belongs to the intended project, enable the Generative Language API, review application/API restrictions, and confirm the model is available to that key. Then repeat the same minimal probe; never paste the key into source, chat, or logs.
- Current gate: live Gemini completion remains unverified until that probe returns a normalized text response and bounded usage. The adapter correctly classifies 401/403 as `authentication` and does not retry automatically.

## Failure classification

- Connection refused / DNS / timeout: `transport`.
- HTTP authentication / quota / 429: adapter must classify into `authentication`, `quota`, or `rate_limit`.
- Unparseable provider response: `provider_decode`.
- Tool result that cannot be correlated to its call: `schema_validation`.
