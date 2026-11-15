from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .paths import FIRMWARE_DIR, METADATA_FILE
from .registry import Artifact, RegistryError, collect_artifacts, validate_package, validate_target


class SyncError(RuntimeError):
    pass


@dataclass
class SyncResult:
    target_id: str
    checked: int = 0
    downloaded: int = 0
    unchanged: int = 0
    skipped: int = 0
    commit: str | None = None
    message: str = ""


class GitHubClient:
    def __init__(self) -> None:
        self.token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self.base_headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "Ultimate-LAB5-Flasher",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            self.base_headers["Authorization"] = f"Bearer {self.token}"

    def request(self, url: str, accept: str | None = None) -> bytes:
        headers = dict(self.base_headers)
        if accept:
            headers["Accept"] = accept
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise SyncError(f"GitHub request failed ({exc.code}) for {url}: {body}") from exc
        except urllib.error.URLError as exc:
            raise SyncError(f"GitHub request failed for {url}: {exc}") from exc

    def json(self, path: str, query: dict[str, str] | None = None) -> Any:
        query_string = ""
        if query:
            query_string = "?" + urllib.parse.urlencode(query)
        url = f"https://api.github.com{path}{query_string}"
        return json.loads(self.request(url).decode("utf-8"))

    def latest_commit_for_path(self, repo: str, branch: str, path: str) -> dict[str, Any] | None:
        data = self.json(
            f"/repos/{repo}/commits",
            {"sha": branch, "path": path.strip("/"), "per_page": "1"},
        )
        if not data:
            return None
        return data[0]

    def directory(self, repo: str, ref: str, path: str) -> list[dict[str, Any]]:
        encoded_path = urllib.parse.quote(path.strip("/"), safe="/")
        data = self.json(f"/repos/{repo}/contents/{encoded_path}", {"ref": ref})
        if not isinstance(data, list):
            raise SyncError(f"GitHub path is not a directory: {repo}/{path}")
        return data

    def download_content(
        self,
        repo: str,
        ref: str,
        full_path: str,
        destination: Path,
        *,
        progress: Callable[[int, int | None], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        encoded_path = urllib.parse.quote(full_path.strip("/"), safe="/")
        query = urllib.parse.urlencode({"ref": ref})
        url = f"https://api.github.com/repos/{repo}/contents/{encoded_path}?{query}"
        headers = dict(self.base_headers)
        headers["Accept"] = "application/vnd.github.raw"
        request = urllib.request.Request(url, headers=headers)
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_suffix(destination.suffix + ".part")
        try:
            with urllib.request.urlopen(request, timeout=30) as response, tmp.open("wb") as handle:
                total_header = response.headers.get("Content-Length")
                total = int(total_header) if total_header and total_header.isdigit() else None
                written = 0
                while True:
                    if cancelled and cancelled():
                        raise SyncError("Download cancelled.")
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    written += len(chunk)
                    if progress:
                        progress(written, total)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            tmp.unlink(missing_ok=True)
            raise SyncError(f"GitHub request failed ({exc.code}) for {url}: {body}") from exc
        except urllib.error.URLError as exc:
            tmp.unlink(missing_ok=True)
            raise SyncError(f"GitHub request failed for {url}: {exc}") from exc
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        tmp.replace(destination)


def load_metadata() -> dict[str, Any]:
    if not METADATA_FILE.exists():
        return {"schema": 1, "targets": {}}
    try:
        return json.loads(METADATA_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SyncError(f"Invalid metadata file: {METADATA_FILE}: {exc}") from exc


def save_metadata(metadata: dict[str, Any]) -> None:
    FIRMWARE_DIR.mkdir(parents=True, exist_ok=True)
    METADATA_FILE.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def target_dir(target_id: str) -> Path:
    return FIRMWARE_DIR / target_id


def package_dir(package_id: str) -> Path:
    return FIRMWARE_DIR / "packages" / package_id


def required_flash_files_exist(target_id: str, target: dict[str, Any]) -> bool:
    base = target_dir(target_id)
    for artifact in collect_artifacts(target, include_extras=False):
        if artifact.required and not (base / artifact.local_name).exists():
            return False
    return True


def _remote_file_map(directory: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["name"]: item for item in directory if item.get("type") == "file"}


def _metadata_target(metadata: dict[str, Any], target_id: str) -> dict[str, Any]:
    return metadata.setdefault("targets", {}).setdefault(target_id, {"files": {}})


def _metadata_package(metadata: dict[str, Any], package_id: str) -> dict[str, Any]:
    return metadata.setdefault("packages", {}).setdefault(package_id, {"files": {}})


def sync_target(
    target_id: str,
    target: dict[str, Any],
    *,
    dry_run: bool = False,
    force: bool = False,
    include_extras: bool = True,
) -> SyncResult:
    if not target.get("enabled", True):
        return SyncResult(target_id=target_id, skipped=1, message="disabled")

    validate_target(target_id, target)
    source = target["source"]
    client = GitHubClient()
    repo = source["repo"]
    branch = source["branch"]
    source_path = source["path"].strip("/")

    commit_info = client.latest_commit_for_path(repo, branch, source_path)
    ref = commit_info["sha"] if commit_info else branch
    commit_sha = commit_info["sha"] if commit_info else None

    remote_dir = _remote_file_map(client.directory(repo, ref, source_path))
    artifacts = collect_artifacts(target, include_extras=include_extras)
    metadata = load_metadata()
    meta_target = _metadata_target(metadata, target_id)
    meta_files = meta_target.setdefault("files", {})
    result = SyncResult(target_id=target_id, checked=len(artifacts), commit=commit_sha)

    base = target_dir(target_id)
    for artifact in artifacts:
        remote = remote_dir.get(artifact.source_name)
        if remote is None:
            if artifact.required:
                raise SyncError(
                    f"Missing required file in {repo}/{source_path}: {artifact.source_name}"
                )
            result.skipped += 1
            continue

        destination = base / artifact.local_name
        previous = meta_files.get(artifact.local_name, {})
        remote_sha = remote.get("sha")
        is_current = (
            destination.exists()
            and previous.get("github_sha") == remote_sha
            and previous.get("source_name") == artifact.source_name
        )
        if is_current and not force:
            result.unchanged += 1
            continue

        if dry_run:
            result.downloaded += 1
            continue

        full_path = f"{source_path}/{artifact.source_name}"
        client.download_content(repo, ref, full_path, destination)
        meta_files[artifact.local_name] = {
            "source_name": artifact.source_name,
            "source_repo": repo,
            "source_path": source_path,
            "source_branch": branch,
            "source_commit": commit_sha,
            "github_sha": remote_sha,
            "size": destination.stat().st_size,
            "sha256": sha256_file(destination),
            "downloaded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        result.downloaded += 1

    if not dry_run:
        # Reload before saving so separately started sync processes do not
        # accidentally drop metadata for other targets.
        latest_metadata = load_metadata()
        latest_meta_target = _metadata_target(latest_metadata, target_id)
        latest_meta_target["files"] = meta_files
        latest_meta_target.update(
            {
                "name": target["name"],
                "source_repo": repo,
                "source_path": source_path,
                "source_branch": branch,
                "source_commit": commit_sha,
                "synced_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            }
        )
        save_metadata(latest_metadata)

    return result


def sync_package(
    package_id: str,
    package: dict[str, Any],
    *,
    dry_run: bool = False,
    force: bool = False,
) -> SyncResult:
    if not package.get("enabled", True):
        return SyncResult(target_id=package_id, skipped=1, message="disabled")

    validate_package(package_id, package)
    source = package["source"]
    client = GitHubClient()
    repo = source["repo"]
    branch = source["branch"]
    source_path = source["path"].strip("/")

    commit_info = client.latest_commit_for_path(repo, branch, source_path)
    ref = commit_info["sha"] if commit_info else branch
    commit_sha = commit_info["sha"] if commit_info else None
    remote_dir = _remote_file_map(client.directory(repo, ref, source_path))

    artifacts = [
        Artifact(
            entry["file"],
            entry.get("source_file", entry["file"]),
            bool(entry.get("required", True)),
            "package",
        )
        for entry in package.get("files", [])
    ]
    metadata = load_metadata()
    meta_package = _metadata_package(metadata, package_id)
    meta_files = meta_package.setdefault("files", {})
    result = SyncResult(target_id=package_id, checked=len(artifacts), commit=commit_sha)
    base = package_dir(package_id)

    for artifact in artifacts:
        remote = remote_dir.get(artifact.source_name)
        if remote is None:
            if artifact.required:
                raise SyncError(f"Missing required file in {repo}/{source_path}: {artifact.source_name}")
            result.skipped += 1
            continue

        destination = base / artifact.local_name
        previous = meta_files.get(artifact.local_name, {})
        remote_sha = remote.get("sha")
        is_current = (
            destination.exists()
            and previous.get("github_sha") == remote_sha
            and previous.get("source_name") == artifact.source_name
        )
        if is_current and not force:
            result.unchanged += 1
            continue

        if dry_run:
            result.downloaded += 1
            continue

        full_path = f"{source_path}/{artifact.source_name}"
        client.download_content(repo, ref, full_path, destination)
        meta_files[artifact.local_name] = {
            "source_name": artifact.source_name,
            "source_repo": repo,
            "source_path": source_path,
            "source_branch": branch,
            "source_commit": commit_sha,
            "github_sha": remote_sha,
            "size": destination.stat().st_size,
            "sha256": sha256_file(destination),
            "downloaded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        result.downloaded += 1

    if not dry_run:
        latest_metadata = load_metadata()
        latest_meta_package = _metadata_package(latest_metadata, package_id)
        latest_meta_package["files"] = meta_files
        latest_meta_package.update(
            {
                "name": package["name"],
                "source_repo": repo,
                "source_path": source_path,
                "source_branch": branch,
                "source_commit": commit_sha,
                "synced_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            }
        )
        save_metadata(latest_metadata)

    return result


def clean_target_cache(target_id: str) -> None:
    base = target_dir(target_id)
    if base.exists():
        shutil.rmtree(base)
    metadata = load_metadata()
    metadata.get("targets", {}).pop(target_id, None)
    save_metadata(metadata)


def describe_cached_target(target_id: str) -> dict[str, Any] | None:
    metadata = load_metadata()
    return metadata.get("targets", {}).get(target_id)


def describe_cached_package(package_id: str) -> dict[str, Any] | None:
    metadata = load_metadata()
    return metadata.get("packages", {}).get(package_id)
