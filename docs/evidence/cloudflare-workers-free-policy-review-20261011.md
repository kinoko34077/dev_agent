# Cloudflare Workers AI public Free-plan pricing review — 2026-10-11

**Evidence classification:** public policy observation only; **not** an account-plan, billing, quota, qualification, health, route-admission, or Provider-generation fact. Supporting owner: dev_agent #81. Formal runtime owner: #65; protected Gate: #11.

## Verified public source and expiry of this review

- Primary: https://developers.cloudflare.com/workers-ai/platform/pricing/ (published site states **Last updated Oct 9, 2026**; manually rechecked **Oct 11, 2026**).
- Cross-check: https://developers.cloudflare.com/workers-ai/platform/limits/ and https://developers.cloudflare.com/workers-ai/platform/errors/ (account/model rate limits and quota errors are separate).
- **Advisory re-review due:** 2026-10-18 12:00 UTC (seven-day inspection horizon only). This date MUST NOT be applied to `TrustedResourceProfile.expires_at` without evidence for the concrete account/billing mode; this document never participates in `BillingResolver`.

The public pricing states that **Workers Free** includes **10,000 Neurons per day** with no automatic paid usage beyond the allocation; limits reset at 00:00 UTC and excess requests fail. **Workers Paid** also includes 10,000 free Neurons but explicitly **charges above** the included allocation. Therefore seeing the 10,000 daily allowance is **not** proof that an arbitrary configured Cloudflare account has no paid overage.

The pricing page lists seven exact models requiring a paid billing method or prepaid AI Gateway credits (the currently accepted denylist agrees):

1. `@cf/moonshotai/kimi-k2.6`
2. `@cf/moonshotai/kimi-k2.7-code`
3. `@cf/zai-org/glm-5.2`
4. `@cf/zai-org/glm-5.3`
5. `@cf/zai-org/glm-5.3-flash`
6. `@cf/deepseek-ai/deepseek-v4-flash-0731`
7. `@cf/deepseek-ai/deepseek-v4-pro-0813`

Models such as `@cf/zai-org/glm-4.7-flash` may be offered on Workers Free according to Cloudflare's July 28 update (https://developers.cloudflare.com/changelog/post/2026-07-28-models-require-workers-paid/), but **offered** does not mean currently qualified, healthy, available, within quota, or admitted for a specific role.

## Exact account facts not yet established

- Whether the configured `cloudflare:account` credential refers to a Workers **Free** account, a **Paid** account with billable overage, or prepaid AI Gateway/unified billing; **UNKNOWN**.
- Whether the account has current free daily Neuron headroom, authoritative/derived consumption and reset, model access, quota-domain identity and health; **UNKNOWN**.
- Whether this account's selected model is still discovered/qualified and meets current benchmark/capability evidence; **UNKNOWN**. The accepted catalog/capability/benchmark observations expired on Oct 11.

**Disposition:** do **not** renew `src/dev_agent/resources/billing_catalog.py` Cloudflare wildcard runtime review window (accepted expiry **2026-10-10T00:00:00Z**) based solely on this public page. Keep unknown account plans/paid-overage risk fail-closed. The 2026-10-18 date is solely the next review of **public pricing**, not a runtime admission deadline or trusted positive entitlement. #65 WO-1 stays `PRECHECK_BLOCKED`, formal route supply 0. No Provider call or credential was used in this review.

## Read-only account-plan observation route (F3, #81 / PR #92)

The public plan review above cannot establish the actual Cloudflare billing mode. Where an **already-authorized** Billing Read credential exists, an operator can run:

```sh
python scripts/probe_cloudflare_subscriptions.py
```

- `AUTH_REQUIRED`: existing `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` are absent; **no network call**; do not generate or request new credentials automatically.
- `AUTH_REJECTED`: an existing credential was sent but the subscription endpoint returned HTTP 401. The probe reports that a network attempt occurred, without printing upstream error text or token.
- `ACCESS_REQUIRED`: the GET subscription API refused current scope (official Cloudflare API requires Account Billing Read or Write); do not elevate permissions automatically.
- `OBSERVED_UNVERIFIED`: bounded redacted list of plan-name **hints** and lifecycle states. No account ID, API token, subscription ID, price, raw plan strings or Cloudflare response body is emitted.

The official endpoint is [List Subscriptions (GET)](https://developers.cloudflare.com/api/resources/accounts/subresources/subscriptions/methods/get/). `OBSERVED_UNVERIFIED` is **not** proof of zero overage: human review must match the exact Workers AI account, whether Workers Paid and AI Gateway prepaid/unified billing are enabled, current daily usage and separate model/qualification evidence. Even `WORKERS_FREE_LABEL_ONLY` or an empty list never authorizes renewal of `TRUSTED_RESOURCE_CATALOG` or Phase 8 admission. The probe does not run automatically in GitHub Actions and does not call any model.
