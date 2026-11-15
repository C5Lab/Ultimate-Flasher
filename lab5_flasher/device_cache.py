from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .github_sync import GitHubClient, SyncError, sha256_file
from .portable import CancellationToken, LogCallback, ProgressCallback
from .registry import Artifact, collect_artifacts, validate_package, validate_target


@dataclass(frozen=True)
class PreparedArtifacts:
    directory: Path
    commit: str | None
    updated: int


def default_cache_root() -> Path:
    configured = os.environ.get("ULTIMATE_FLASHER_CACHE")
    if configured:
        return Path(configured).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / "ultimate-flasher"


def _safe_name(name: str) -> str:
    path = Path(name)
    if path.name != name or name in {"", ".", ".."}:
        raise SyncError(f"Unsafe artifact name in registry: {name!r}")
    return name


def _load_metadata(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": 1, "files": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": 1, "files": {}}
    return data if data.get("schema") == 1 else {"schema": 1, "files": {}}


def _git_blob_sha(path: Path) -> str:
    size = path.stat().st_size
    digest = hashlib.sha1(usedforsecurity=False)
    digest.update(f"blob {size}\0".encode("ascii"))
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prepare(
    item_id: str,
    source: dict[str, Any],
    artifacts: list[Artifact],
    base: Path,
    token: CancellationToken,
    progress: ProgressCallback,
    log: LogCallback,
) -> PreparedArtifacts:
    base.mkdir(parents=True, exist_ok=True)
    metadata_path = base / ".metadata.json"
    previous = _load_metadata(metadata_path)
    previous_files = previous.get("files", {})
    client = GitHubClient()
    source_path = source["path"].strip("/")
    commit_info = client.latest_commit_for_path(source["repo"], source["branch"], source_path)
    ref = commit_info["sha"] if commit_info else source["branch"]
    commit = commit_info["sha"] if commit_info else None
    remote = {
        entry["name"]: entry
        for entry in client.directory(source["repo"], ref, source_path)
        if entry.get("type") == "file"
    }

    required: list[tuple[Artifact, dict[str, Any]]] = []
    for artifact in artifacts:
        _safe_name(artifact.local_name)
        info = remote.get(artifact.source_name)
        if not info:
            if artifact.required:
                raise SyncError(f"Missing required firmware file: {artifact.source_name}")
            continue
        required.append((artifact, info))

    changed: list[tuple[Artifact, dict[str, Any]]] = []
    for artifact, info in required:
        local = base / artifact.local_name
        meta = previous_files.get(artifact.local_name, {})
        current = local.exists() and (
            (meta.get("github_sha") == info.get("sha") and meta.get("sha256") == sha256_file(local))
            or _git_blob_sha(local) == info.get("sha")
        )
        if not current:
            changed.append((artifact, info))

    if not changed:
        progress("download", 1.0, "Firmware cache is current")
        log(f"Cache is current for {item_id} @ {ref[:12]}.")
        return PreparedArtifacts(base, commit, 0)

    total_bytes = sum(int(info.get("size", 0)) for _, info in changed)
    completed = 0
    stage = Path(tempfile.mkdtemp(prefix=f".{item_id}-stage-", dir=base.parent))
    staged: list[tuple[Artifact, dict[str, Any], Path]] = []
    try:
        for artifact, info in changed:
            token.raise_if_cancelled()
            size = int(info.get("size", 0))
            output = stage / artifact.local_name
            log(f"Downloading update {artifact.source_name} @ {ref[:12]}.")

            def report(done: int, _total: int | None, *, offset: int = completed, name: str = artifact.local_name) -> None:
                fraction = (offset + done) / total_bytes if total_bytes else 0.0
                progress("download", min(1.0, fraction), name)

            client.download_content(
                source["repo"],
                ref,
                f"{source_path}/{artifact.source_name}",
                output,
                progress=report,
                cancelled=lambda: token.cancelled,
            )
            if _git_blob_sha(output) != info.get("sha"):
                raise SyncError(f"Git blob verification failed for {artifact.source_name}.")
            staged.append((artifact, info, output))
            completed += size

        new_files: dict[str, Any] = {}
        for artifact, info in required:
            local = base / artifact.local_name
            staged_item = next((entry for entry in staged if entry[0].local_name == artifact.local_name), None)
            if staged_item:
                staged_item[2].replace(local)
            new_files[artifact.local_name] = {
                "source_name": artifact.source_name,
                "github_sha": info.get("sha"),
                "size": local.stat().st_size,
                "sha256": sha256_file(local),
            }

        # Remove only files previously owned by this cache entry and no longer present in the registry.
        for old_name in set(previous_files) - set(new_files):
            old_path = base / _safe_name(old_name)
            old_path.unlink(missing_ok=True)

        new_metadata = {
            "schema": 1,
            "item_id": item_id,
            "source_repo": source["repo"],
            "source_path": source_path,
            "source_commit": commit,
            "files": new_files,
        }
        metadata_tmp = metadata_path.with_suffix(".json.part")
        metadata_tmp.write_text(json.dumps(new_metadata, indent=2) + "\n", encoding="utf-8")
        metadata_tmp.replace(metadata_path)
        progress("download", 1.0, f"Updated {len(changed)} file(s)")
        return PreparedArtifacts(base, commit, len(changed))
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def prepare_target_cache(
    target_id: str,
    target: dict[str, Any],
    token: CancellationToken,
    progress: ProgressCallback,
    log: LogCallback,
    cache_root: Path | None = None,
) -> PreparedArtifacts:
    validate_target(target_id, target)
    root = cache_root or default_cache_root()
    return _prepare(
        target_id,
        target["source"],
        collect_artifacts(target, include_extras=False),
        root / "firmware" / target_id,
        token,
        progress,
        log,
    )


def prepare_package_cache(
    package_id: str,
    package: dict[str, Any],
    token: CancellationToken,
    progress: ProgressCallback,
    log: LogCallback,
    cache_root: Path | None = None,
) -> PreparedArtifacts:
    validate_package(package_id, package)
    artifacts = [
        Artifact(
            entry["file"],
            entry.get("source_file", entry["file"]),
            bool(entry.get("required", True)),
            "package",
        )
        for entry in package.get("files", [])
    ]
    root = cache_root or default_cache_root()
    return _prepare(
        package_id,
        package["source"],
        artifacts,
        root / "packages" / package_id,
        token,
        progress,
        log,
    )
