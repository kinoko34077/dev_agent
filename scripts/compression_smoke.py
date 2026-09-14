"""Run one bounded, non-secret smoke request against the fixed compressor.

This is an operator-invoked check, not part of the normal Provider pool or
G6O1-SIM.  It prints only safe response metadata and never prints the token,
request text, or response body.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dev_agent.compression import (
    DEFAULT_COMPRESSION_THRESHOLD_CHARS,
    CompressionHttpError,
    HttpCompressionService,
)


def _smoke_payload(size: int) -> str:
    prefix = "compression-smoke 2026-09-14 src/dev_agent/handoff/protocol.py "
    if size < len(prefix):
        return prefix[:size]
    return prefix + (" bounded payload" * ((size - len(prefix)) // 15 + 1))[: size - len(prefix)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="bounded fixed-profile Compression Service smoke")
    parser.add_argument(
        "--payload-chars",
        type=int,
        default=DEFAULT_COMPRESSION_THRESHOLD_CHARS + 1,
        help="Unicode code-point count for the safe generated payload",
    )
    args = parser.parse_args(argv)
    if args.payload_chars <= DEFAULT_COMPRESSION_THRESHOLD_CHARS or args.payload_chars > 20_000:
        parser.error("--payload-chars must be between 3001 and 20000")

    try:
        service = HttpCompressionService.from_environment()
        result = service.compress(_smoke_payload(args.payload_chars))
    except CompressionHttpError as exc:
        print(
            json.dumps(
                {
                    "status": "not_verified",
                    "failure_category": exc.category,
                    "http_status": exc.http_status,
                },
                ensure_ascii=False,
            )
        )
        return 2

    print(
        json.dumps(
            {
                "status": "verified",
                "profile": result.profile,
                "prompt_version": result.prompt_version,
                "model": result.model,
                "input_chars": result.input_chars,
                "output_chars": result.output_chars,
                "input_sha256": result.input_sha256,
                "output_sha256": result.output_sha256,
                "warnings": list(result.warnings),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
