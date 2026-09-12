"""Fixed compression service contract, independent of Provider routing."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Protocol


COMPRESSION_PROFILE = "semantic-dense-v1"


def _text(value: object, name: str, *, max_length: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    value = value.strip()
    if len(value) > max_length:
        raise ValueError(f"{name} is too long")
    return value


@dataclass(frozen=True)
class CompressionResult:
    compressed_text: str
    profile: str
    prompt_version: str
    model: str
    input_chars: int
    output_chars: int
    input_sha256: str
    output_sha256: str
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.compressed_text, str) or not self.compressed_text.strip():
            raise ValueError("compressed_text must be a non-empty string")
        object.__setattr__(self, "profile", _text(self.profile, "profile"))
        object.__setattr__(self, "prompt_version", _text(self.prompt_version, "prompt_version"))
        object.__setattr__(self, "model", _text(self.model, "model"))
        for name in ("input_chars", "output_chars"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name in ("input_sha256", "output_sha256"):
            value = _text(getattr(self, name), name, max_length=64).lower()
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError(f"{name} must be a SHA-256 hex digest")
            object.__setattr__(self, name, value)
        if isinstance(self.warnings, (str, bytes)):
            raise TypeError("warnings must be a sequence")
        object.__setattr__(self, "warnings", tuple(_text(item, "warnings[]", max_length=512) for item in self.warnings))

    @classmethod
    def from_values(cls, *, compressed_text: str, profile: str, prompt_version: str, model: str, original_text: str, warnings: tuple[str, ...] = ()) -> "CompressionResult":
        output_bytes = compressed_text.encode("utf-8")
        input_bytes = original_text.encode("utf-8")
        return cls(
            compressed_text=compressed_text,
            profile=profile,
            prompt_version=prompt_version,
            model=model,
            input_chars=len(original_text),
            output_chars=len(compressed_text),
            input_sha256=hashlib.sha256(input_bytes).hexdigest(),
            output_sha256=hashlib.sha256(output_bytes).hexdigest(),
            warnings=warnings,
        )


class CompressionService(Protocol):
    def compress(self, text: str, *, profile: str = COMPRESSION_PROFILE) -> CompressionResult:
        ...


__all__ = ["COMPRESSION_PROFILE", "CompressionResult", "CompressionService"]
