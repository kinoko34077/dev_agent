"""Host-owned revision-pinned runtime release materialization."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any


_REVISION = re.compile(r"^[0-9a-fA-F]{40,64}$")
_SCHEMA_VERSION = 1


class RuntimeReleaseError(RuntimeError):
    """A pinned runtime release could not be safely created or reused."""


def _revision_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeReleaseError("revision must be non-empty text")
    value = value.strip()
    if len(value) > 200 or any(character.isspace() or ord(character) < 32 for character in value):
        raise RuntimeReleaseError("revision contains unsafe characters")
    return value


@dataclass(frozen=True)
class RuntimeRelease:
    """An immutable identity projection for one release worktree."""

    revision: str
    runtime_root: Path
    metadata_path: Path

    def __post_init__(self) -> None:
        revision = _revision_text(self.revision)
        if _REVISION.fullmatch(revision) is None:
            raise RuntimeReleaseError("release revision must be a full Git commit id")
        runtime_root = Path(self.runtime_root)
        metadata_path = Path(self.metadata_path)
        if not runtime_root.is_absolute() or not metadata_path.is_absolute():
            raise RuntimeReleaseError("release paths must be absolute")
        object.__setattr__(self, "revision", revision.lower())
        object.__setattr__(self, "runtime_root", runtime_root)
        object.__setattr__(self, "metadata_path", metadata_path)

    def to_dict(self) -> dict[str, str | int]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "revision": self.revision,
            "runtime_root": str(self.runtime_root),
            "metadata_path": str(self.metadata_path),
        }


class RevisionPinnedRuntimeStore:
    """Materialize clean Git worktrees outside a mutable development checkout."""

    def __init__(self, source_root: str | Path, releases_root: str | Path) -> None:
        self.source_root = Path(source_root).resolve()
        self.releases_root = Path(releases_root).resolve()
        if not self.source_root.is_dir():
            raise RuntimeReleaseError("source_root must be an existing directory")
        if self.releases_root == self.source_root:
            raise RuntimeReleaseError("releases_root must be separate from source_root")
        try:
            self.releases_root.relative_to(self.source_root)
        except ValueError:
            pass
        else:
            raise RuntimeReleaseError("releases_root must be outside source_root")
        self.releases_root.mkdir(parents=True, exist_ok=True)
        self.metadata_root = self.releases_root / ".metadata"
        self.metadata_root.mkdir(parents=True, exist_ok=True)

    def materialize(self, revision: str) -> RuntimeRelease:
        """Resolve, create, and verify one exact commit release.

        Existing partial or modified releases are rejected and never replaced.
        A source checkout may be dirty after the release is created; the
        release remains pinned to the original commit.
        """

        resolved_revision = self._resolve_revision(revision)
        runtime_root = self.releases_root / resolved_revision
        metadata_path = self.metadata_root / f"{resolved_revision}.json"
        if runtime_root.is_symlink() or metadata_path.is_symlink():
            raise RuntimeReleaseError("runtime release paths cannot be symlinks")
        if runtime_root.exists() or metadata_path.exists():
            if not runtime_root.is_dir() or not metadata_path.is_file():
                raise RuntimeReleaseError("runtime release already exists partially")
            self._validate_metadata(metadata_path, resolved_revision)
            self._verify_release(runtime_root, resolved_revision)
            return RuntimeRelease(resolved_revision, runtime_root, metadata_path)

        self.releases_root.mkdir(parents=True, exist_ok=True)
        result = self._run_git(
            self.source_root,
            "worktree",
            "add",
            "--detach",
            runtime_root.as_posix(),
            resolved_revision,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeReleaseError(f"could not create pinned runtime release: {detail[:500]}")
        self._verify_release(runtime_root, resolved_revision)
        self._write_metadata(metadata_path, resolved_revision)
        return RuntimeRelease(resolved_revision, runtime_root, metadata_path)

    def revision_reachable_from(self, revision: str, trusted_ref: str) -> bool:
        """Return whether ``revision`` is contained in one trusted Git ref.

        This is a read-only source check for callers such as local self-update.
        It does not grant process or promotion authority and never changes the
        mutable checkout.
        """

        resolved_revision = self._resolve_revision(revision)
        trusted = _revision_text(trusted_ref)
        ref = self._run_git(self.source_root, "rev-parse", "--verify", f"{trusted}^{{commit}}")
        if ref.returncode != 0:
            return False
        result = self._run_git(self.source_root, "merge-base", "--is-ancestor", resolved_revision, trusted)
        return result.returncode == 0

    def create_rollback_proof(
        self,
        revision: str,
        *,
        health_status: str,
        verified_at: str,
    ) -> Any:
        """Materialize a clean release and return bounded rollback evidence.

        Materialization verifies the exact revision, metadata, and clean
        worktree without starting the runtime or mutating the source
        checkout.  The caller supplies the result of its separately bounded
        health check; the returned proof is still only evidence and grants no
        rollback or integration authority.
        """

        release = self.materialize(revision)
        from ..intelligence.self_repair import RollbackProof

        return RollbackProof.create(
            revision=release.revision,
            release_ref=f".devfarm/runtime-releases/{release.revision}",
            health_status=health_status,
            verified_at=verified_at,
        )

    def _resolve_revision(self, revision: str) -> str:
        requested = _revision_text(revision)
        result = self._run_git(self.source_root, "rev-parse", "--verify", f"{requested}^{{commit}}")
        resolved = result.stdout.strip()
        if result.returncode != 0 or _REVISION.fullmatch(resolved) is None:
            raise RuntimeReleaseError("could not resolve revision to a full commit id")
        return resolved.lower()

    def _verify_release(self, runtime_root: Path, revision: str) -> None:
        if not runtime_root.is_dir():
            raise RuntimeReleaseError("runtime release worktree is missing")
        head = self._run_git(runtime_root, "rev-parse", "--verify", "HEAD^{commit}")
        if head.returncode != 0 or head.stdout.strip().lower() != revision:
            raise RuntimeReleaseError("runtime release HEAD does not match pinned revision")
        clean = self._run_git(runtime_root, "status", "--porcelain=v1", "--untracked-files=all")
        if clean.returncode != 0:
            raise RuntimeReleaseError("runtime release status could not be verified")
        if clean.stdout.strip():
            raise RuntimeReleaseError("release worktree is not clean")

    def _validate_metadata(self, metadata_path: Path, revision: str) -> None:
        try:
            document = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeReleaseError("runtime release metadata is unreadable") from exc
        if not isinstance(document, dict) or document.get("schema_version") != _SCHEMA_VERSION or document.get("revision") != revision:
            raise RuntimeReleaseError("runtime release metadata does not match the pinned revision")

    @staticmethod
    def _write_metadata(metadata_path: Path, revision: str) -> None:
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=metadata_path.parent,
                prefix=f".{metadata_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(
                    {"schema_version": _SCHEMA_VERSION, "revision": revision},
                    temporary,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, metadata_path)
        except OSError as exc:
            raise RuntimeReleaseError("runtime release metadata could not be persisted") from exc
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def _run_git(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={self.source_root.as_posix()}",
                "-c",
                f"safe.directory={cwd.as_posix()}",
                *args,
            ],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )


__all__ = ["RevisionPinnedRuntimeStore", "RuntimeRelease", "RuntimeReleaseError"]
