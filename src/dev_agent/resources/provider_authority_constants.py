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

# Loopback hostnames that are valid for local providers.
LOOPBACK_HOSTNAMES: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})

# Approved origin prefixes per cloud provider.  A base_url must start with the
# corresponding prefix (after stripping trailing slash).  Override via base_url
# is rejected unless the overriding value is the approved prefix itself (allows
# operators to pass the canonical URL explicitly without being rejected, while
# blocking any deviation).
APPROVED_CLOUD_ORIGINS: dict[str, str] = {
    "gemini": "https://generativelanguage.googleapis.com",
    "cloudflare": "https://api.cloudflare.com",
    "openrouter": "https://openrouter.ai",
    "openai": "https://api.openai.com",
    "groq": "https://api.groq.com",
    "mistral": "https://api.mistral.ai",
    "sambanova": "https://api.sambanova.ai",
    "ollama_cloud": "https://ollama.com",
    "vercel": "https://ai-gateway.vercel.sh",
}

# Canonical api_key_env variable names per cloud provider.
# Overriding api_key_env to a different name is rejected in the production path.
APPROVED_API_KEY_ENVS: dict[str, frozenset[str]] = {
    "gemini": frozenset({
        "GEMINI_API_KEY",
        # Numbered project slots configured in operation.py (_CONFIGURED_GEMINI_PROJECTS)
        "GEMINI_API_KEY_2", "GEMINI_API_KEY_3", "GEMINI_API_KEY_4", "GEMINI_API_KEY_5",
    }),
    "cloudflare": frozenset({"CLOUDFLARE_API_TOKEN"}),
    "openrouter": frozenset({"OPENROUTER_API_KEY"}),
    "openai": frozenset({"OPENAI_API_KEY"}),
    "groq": frozenset({"GROQ_API_KEY"}),
    "mistral": frozenset({"MISTRAL_API_KEY"}),
    "sambanova": frozenset({"SAMBANOVA_API_KEY"}),
    "ollama_cloud": frozenset({"OLLAMA_API_KEY"}),
    "vercel": frozenset({"AI_GATEWAY_API_KEY"}),
}

# provider_ids with a real network endpoint concept (base_url/api_key_env are
# meaningful for them).  Only these identities are subject to the exact-type
# canonical-adapter check in provider_policy.validate_provider_class_identity
# — "fake" and any other locally-invented test identity have no endpoint to
# protect and are exempt. The concrete class allowlist itself lives in
# providers/canonical_types.py (the same SSOT ProviderFactory constructs
# from), not here, so this module can stay a plain provider_id set.
NETWORK_CAPABLE_PROVIDER_IDS: frozenset[str] = REAL_LOCAL_PROVIDER_IDS | frozenset(APPROVED_CLOUD_ORIGINS)
