from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from .flasher import FlashError
from .github_sync import GitHubClient, SyncError
from .portable import CancellationToken, FlashResult, LogCallback, OperationCancelled, ProgressCallback, serial_devices
from .registry import validate_package


FLIPPER_VID = 0x0483
FLIPPER_PID = 0x5740


def flipper_ports() -> list[str]:
    return [
        item.port
        for item in serial_devices()
        if item.vid == FLIPPER_VID and item.pid == FLIPPER_PID
    ]


def _download_package(
    package_id: str,
    package: dict[str, Any],
    destination: Path,
    token: CancellationToken,
    progress: ProgressCallback,
) -> tuple[Path, str | None]:
    validate_package(package_id, package)
    files = package.get("files", [])
    if len(files) != 1:
        raise SyncError("Flipper installation currently requires exactly one .fap artifact.")
    entry = files[0]
    if not entry["file"].lower().endswith(".fap"):
        raise SyncError("Refusing to install a non-.fap package through the Flipper RPC backend.")
    source = package["source"]
    source_path = source["path"].strip("/")
    client = GitHubClient()
    commit_info = client.latest_commit_for_path(source["repo"], source["branch"], source_path)
    ref = commit_info["sha"] if commit_info else source["branch"]
    commit = commit_info["sha"] if commit_info else None
    remote_name = entry.get("source_file", entry["file"])
    remote = {item["name"]: item for item in client.directory(source["repo"], ref, source_path)}
    if remote_name not in remote:
        raise SyncError(f"Missing package file in source repository: {remote_name}")
    output = destination / entry["file"]
    client.download_content(
        source["repo"],
        ref,
        f"{source_path}/{remote_name}",
        output,
        progress=lambda done, total: progress(
            "download", (done / total) if total else 0.0, entry["file"]
        ),
        cancelled=lambda: token.cancelled,
    )
    progress("download", 1.0, "FAP downloaded")
    return output, commit


def install_fap_ephemeral(
    package_id: str,
    package: dict[str, Any],
    port: str,
    *,
    confirmed: bool,
    token: CancellationToken | None = None,
    progress: ProgressCallback | None = None,
    log: LogCallback | None = None,
) -> FlashResult:
    if not confirmed:
        raise FlashError("Explicit user confirmation is required before installing a FAP.")
    if port not in flipper_ports():
        raise FlashError("Selected USB serial device is not a Flipper Zero (expected USB 0483:5740).")
    if package.get("transport") != "flipper-rpc":
        raise FlashError("Package is not configured for Flipper protobuf RPC installation.")
    remote_path = package.get("install_path")
    if not remote_path or not str(remote_path).startswith("/ext/apps/"):
        raise FlashError("Package has no safe /ext/apps installation path.")

    token = token or CancellationToken()
    progress = progress or (lambda _stage, _value, _label: None)
    log = log or (lambda _line: None)
    token.raise_if_cancelled()

    try:
        from flipperzero_protobuf.flipper_proto import FlipperProto
    except ImportError as exc:
        raise FlashError(
            "Missing flipperzero-protobuf; run CardputerZero/install.sh to install the RPC backend."
        ) from exc

    with tempfile.TemporaryDirectory(prefix=f"ultimate-flasher-{package_id}-") as temp_name:
        local_path, commit = _download_package(
            package_id, package, Path(temp_name), token, progress
        )
        token.raise_if_cancelled()
        payload = local_path.read_bytes()
        local_md5 = hashlib.md5(payload, usedforsecurity=False).hexdigest()
        proto = None
        try:
            log(f"Opening Flipper protobuf RPC on {port}.")
            proto = FlipperProto(serial_port=port)
            parent = PurePosixPath(str(remote_path)).parent
            current = PurePosixPath("/")
            for component in parent.parts[1:]:
                token.raise_if_cancelled()
                current /= component
                try:
                    proto.rpc_mkdir(str(current))
                except Exception:
                    # Existing directories are expected; stat distinguishes that from an RPC failure.
                    proto.rpc_stat(str(current))
            token.raise_if_cancelled()
            progress("install", 0.0, f"Uploading {len(payload)} bytes")
            proto.rpc_write(str(remote_path), payload)
            progress("install", 1.0, "Upload complete")
            token.raise_if_cancelled()
            remote_md5 = proto.rpc_md5sum(str(remote_path)).lower()
            if remote_md5 != local_md5:
                raise FlashError(
                    f"FAP verification failed: device MD5 {remote_md5}, local MD5 {local_md5}."
                )
            progress("verify", 1.0, "FAP MD5 verified")
            return FlashResult(package_id, port, "flipperzero", commit)
        except OperationCancelled:
            raise
        except FlashError:
            raise
        except Exception as exc:
            raise FlashError(f"Flipper RPC installation failed: {exc}") from exc
        finally:
            if proto is not None:
                try:
                    proto._serial.close()  # upstream library currently has no public close method
                except Exception:
                    pass
