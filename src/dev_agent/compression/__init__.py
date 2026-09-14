"""Independent, fixed-profile payload compression boundary."""

from .client import (
    CompressionDiagnosticCode,
    CompressionFailureCategory,
    CompressionHttpError,
    CompressionIntegrityError,
    HttpCompressionService,
    compress_handoff_payload,
    parse_compression_response,
)
from .integrity import RetentionWarning, inspect_information_retention
from .protocol import (
    COMPRESSION_API_TOKEN_ENV,
    COMPRESSION_PROFILE,
    COMPRESSION_KEYRING_SERVICE,
    COMPRESSION_KEYRING_USERNAME,
    DEFAULT_COMPRESSION_ENDPOINT,
    DEFAULT_COMPRESSION_PROVIDER_CONTEXT_LIMIT_CHARS,
    DEFAULT_COMPRESSION_PUBLIC_MAX_INPUT_CHARS,
    DEFAULT_COMPRESSION_THRESHOLD_CHARS,
    CompressionResult,
    CompressionService,
)

__all__ = [
    "COMPRESSION_PROFILE",
    "COMPRESSION_API_TOKEN_ENV",
    "COMPRESSION_KEYRING_SERVICE",
    "COMPRESSION_KEYRING_USERNAME",
    "DEFAULT_COMPRESSION_ENDPOINT",
    "DEFAULT_COMPRESSION_PUBLIC_MAX_INPUT_CHARS",
    "DEFAULT_COMPRESSION_PROVIDER_CONTEXT_LIMIT_CHARS",
    "DEFAULT_COMPRESSION_THRESHOLD_CHARS",
    "CompressionFailureCategory",
    "CompressionDiagnosticCode",
    "CompressionHttpError",
    "CompressionIntegrityError",
    "CompressionResult",
    "CompressionService",
    "HttpCompressionService",
    "RetentionWarning",
    "compress_handoff_payload",
    "inspect_information_retention",
    "parse_compression_response",
]
