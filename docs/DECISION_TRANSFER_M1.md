# Decision transfer M1 — evidence-bounded diagnostic pilot

Owner: [dev_agent #120](https://github.com/kinoko34077/dev_agent/issues/120) / cross-repo [devflow #761](https://github.com/kinoko34077/devflow/issues/761). **Status: pilot/candidate only.** No production runtime, provider, permission, acceptance or Phase 8 gate change.

## What is implemented

This pilot is not a RAG index or a learned language model. It is a bounded, source-linked **diagnostic decision contract** that turns one reconstructed failure mechanism into an advisory check on a different development case. A reviewing agent must first inspect live Issues and independently structure the packet; the validator cannot read GitHub itself or prove a natural-language claim.

1. **Historical reconstruction input:** `origin.goal`, `success_condition`, typed `observations`, `attempts`, `cause` with explicit provenance and user `correction_ref`. Attempt equivalence is `(probe, revision, result_observation_id)`; repeated identical evidence is not counted as progress.
2. **Candidate mechanism:** `producer_consumer_scope_mismatch`. A setting or state can be present at its producer and absent at a consuming runtime/extension; one endpoint's success does not imply another endpoint's success. This is a *conditional diagnostic strategy*, not an assertion about the cause of every symptom.
3. **Transfer:** independently sourced `target` observations distinguish direct observation, user report, code inspection, and unverified agent report. An unresolved user report does not become an authenticated runtime observation. Conflicting observations trigger reconciliation.
4. **Next action:** `PROBE_BOUNDARY` (both endpoints directly observed), `NEEDS_EVIDENCE` (reports or unknown state), or `NOT_APPLICABLE` (counterexample). The generated probe is a **diagnostic choice**, not authorization to change credentials or deploy.
5. **Commander integration:** `python scripts/devfarm.py decision-check spec/v2/decision-learning/issue31-gesture203.v1.json` runs the evaluator. `python scripts/devfarm.py plan PLAN.json --decision-check PACKET.json` emits a pre-persistence diagnostic advisory alongside the existing goal review. Existing plans without the optional flag keep their behavior; no separate scheduler or Gate.

## Evidence provenance and status

- **Origin #31:** user repeatedly reported secrets saved; public postmortem describes build settings present and Worker runtime missing, repeated low-information probes, and eventual scope mismatch diagnosis. The code uses structured, manually verified issue evidence; the stored fixture does **not** reproduce or publish the private Cloudflare PDF, credentials or logs. Production OAuth completion remains unverified.
- **Transfer #203:** user reported a Main App settings change and keyboard result not reflecting that change. This is not independent proof of which App Group/container/extension actually read the saved value. The expected verdict is **NEEDS_EVIDENCE**, with an observable producer/consumer comparison before asserting root cause.
- **Counterexamples:** producer absent, consumer already present, conflicting reports, agent claims without direct observation, duplicate same-revision probes, and training case reused as transfer are tested. Hypothetical direct-observation variants in unit tests are synthetic and **must not** be recorded as physical evidence.
- **Rule status:** always `candidate`. No assertion of independently validated generalization, user-approved global policy, authorization, or positive real-world improvement.

## Contract limits and next exit

`schema_version: 1` uses explicit Issue URLs and bounded arrays. It stores only evidence classification and counts in output, not arbitrary fixed-constraint contents or unrestricted excerpts. The validator rejects malformed/unknown source shapes and duplicate observation identifiers, but **does not fetch or cryptographically attest the underlying Issues**. Source reading, semantic adjudication, external observations, and conditional selection remain trusted-worker responsibilities; fake `direct_observation` tags in an input cannot be disproved by this module.

Before promoting this rule or claiming an improvement, M2 must connect it to an actual proposer/reviewer decision at the existing Commander/goal-alignment choice point, including whether the agent followed the advice. M3 must compare pre-specified outcomes on sealed unseen cases using the same budget, baseline vs intervention, and measure redundant attempts, root-cause accuracy, false-positive transfers, user correction counts, goal satisfaction, and latency. **A passing unit test is not that evaluation.**

M1 acceptance: source-linked pilot fixture; bounded parser; positive, negative and boundary tests; existing Commander pre-plan surfacing; exact-head CI plus appropriate review; readback after any safe integration. M2+ are explicitly not accepted by M1.
