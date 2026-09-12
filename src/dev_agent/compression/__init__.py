"""Independent, fixed-profile payload compression boundary."""

from .client import (
    CompressionHttpError,
    CompressionIntegrityError,
    HttpCompressionService,
    compress_handoff_payload,
    parse_compression_response,
)
from .integrity import RetentionWarning, inspect_information_retention
from .protocol import COMPRESSION_PROFILE, CompressionResult, CompressionService

__all__ = [
    "COMPRESSION_PROFILE",
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
