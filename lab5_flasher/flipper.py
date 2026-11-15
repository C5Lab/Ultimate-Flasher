from __future__ import annotations

import hashlib
import threading
from pathlib import Path, PurePosixPath
from typing import Any

from .flasher import FlashError
from .github_sync import SyncError
from .portable import CancellationToken, FlashResult, LogCallback, OperationCancelled, ProgressCallback, serial_devices


FLIPPER_VID = 0x0483
FLIPPER_PID = 0x5740


def flipper_ports() -> list[str]:
    return [
        item.port
        for item in serial_devices()
        if item.vid == FLIPPER_VID and item.pid == FLIPPER_PID
    ]


def install_fap_ephemeral(
    package_id: str,
    package: dict[str, Any],
    port: str,
    *,
    confirmed: bool,
    token: CancellationToken | None = None,
    progress: ProgressCallback | None = None,
    log: LogCallback | None = None,
    cache_root: Path | None = None,
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

    from .device_cache import prepare_package_cache

    prepared = prepare_package_cache(package_id, package, token, progress, log, cache_root)
    files = package.get("files", [])
    if len(files) != 1 or not files[0]["file"].lower().endswith(".fap"):
        raise SyncError("Flipper installation requires exactly one .fap artifact.")
    local_path = prepared.directory / files[0]["file"]
    token.raise_if_cancelled()
    payload = local_path.read_bytes()
    local_md5 = hashlib.md5(payload, usedforsecurity=False).hexdigest()
    proto = None
    connection = None
    watcher_done = threading.Event()
    try:
        log(f"Opening Flipper protobuf RPC on {port}.")
        import serial

        connection = serial.Serial(port, baudrate=230400, timeout=None)
        connection.reset_output_buffer()
        connection.reset_input_buffer()
        proto = FlipperProto(serial_port=connection)
        proto.device_info = proto._get_startup_info()
        proto.start_rpc_session()

        def cancel_watcher() -> None:
            while not watcher_done.wait(0.1):
                if token.cancelled:
                    try:
                        connection.close()
                    except Exception:
                        pass
                    return

        threading.Thread(target=cancel_watcher, daemon=True).start()
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
        return FlashResult(package_id, port, "flipperzero", prepared.commit)
    except OperationCancelled:
        raise
    except FlashError:
        raise
    except Exception as exc:
        if token.cancelled:
            raise OperationCancelled("Operation cancelled by user.") from exc
        raise FlashError(f"Flipper RPC installation failed: {exc}") from exc
    finally:
        watcher_done.set()
        if proto is not None:
            try:
                proto._serial.close()  # upstream library currently has no public close method
            except Exception:
                pass
        elif connection is not None:
            try:
                connection.close()
            except Exception:
                pass


install_fap_cached = install_fap_ephemeral
