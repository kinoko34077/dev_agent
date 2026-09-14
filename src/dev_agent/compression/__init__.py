"""Independent, fixed-profile payload compression boundary."""

from .client import (
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
    DEFAULT_COMPRESSION_ENDPOINT,
    DEFAULT_COMPRESSION_THRESHOLD_CHARS,
    CompressionResult,
    CompressionService,
)

__all__ = [
    "COMPRESSION_PROFILE",
    "COMPRESSION_API_TOKEN_ENV",
    "DEFAULT_COMPRESSION_ENDPOINT",
    "DEFAULT_COMPRESSION_THRESHOLD_CHARS",
    "CompressionFailureCategory",
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
