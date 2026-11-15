from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .flasher import FlashError, build_flash_command
from .github_sync import GitHubClient, SyncError
from .registry import collect_artifacts, flash_entries, validate_target


ProgressCallback = Callable[[str, float, str], None]
LogCallback = Callable[[str], None]


class OperationCancelled(FlashError):
    pass


class IncompatibleDevice(FlashError):
    pass


@dataclass(frozen=True)
class SerialDevice:
    port: str
    description: str = ""
    vid: int | None = None
    pid: int | None = None
    readable: bool = False
    writable: bool = False


@dataclass(frozen=True)
class FlashResult:
    target_id: str
    port: str
    detected_chip: str
    source_commit: str | None


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise OperationCancelled("Operation cancelled by user.")


def serial_devices() -> list[SerialDevice]:
    try:
        from serial.tools import list_ports
    except ImportError as exc:
        raise FlashError("Missing pyserial; install the CardputerZero package dependencies.") from exc

    devices = []
    for item in list_ports.comports():
        devices.append(
            SerialDevice(
                port=item.device,
                description=item.description or item.product or "USB serial",
                vid=item.vid,
                pid=item.pid,
                readable=os.access(item.device, os.R_OK),
                writable=os.access(item.device, os.W_OK),
            )
        )
    return sorted(devices, key=lambda item: item.port)


def _normalise_chip(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _esptool_command() -> list[str]:
    return [sys.executable, "-m", "esptool"]


def _run_capture(command: list[str], timeout: float = 20.0) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise FlashError("Timed out while communicating with the USB device.") from exc
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if result.returncode != 0:
        raise FlashError(output.strip() or f"Command failed with code {result.returncode}.")
    return output


def detect_esp_chip(port: str) -> str:
    output = _run_capture(_esptool_command() + ["--port", port, "--chip", "auto", "chip-id"])
    patterns = (
        r"Chip is\s+(ESP32[-A-Za-z0-9]+)",
        r"Detecting chip type\.\.\.\s*(ESP32[-A-Za-z0-9]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, output, flags=re.IGNORECASE)
        if match:
            return _normalise_chip(match.group(1))
    raise FlashError("esptool connected, but did not report a recognizable chip type.")


def require_compatible_chip(target: dict[str, Any], detected_chip: str) -> None:
    expected = _normalise_chip(str(target["chip"]))
    actual = _normalise_chip(detected_chip)
    if actual != expected:
        raise IncompatibleDevice(
            f"Incompatible chip: firmware requires {target['chip']}, device reports {detected_chip}."
        )


def _send_pre_flash_command(target: dict[str, Any], port: str, log: LogCallback) -> None:
    config = target.get("pre_flash")
    if not config or not config.get("serial_command"):
        return
    try:
        import serial

        payload = str(config["serial_command"]).encode("utf-8")
        with serial.Serial(port, int(config.get("baud", 115200)), timeout=1) as connection:
            connection.write(payload)
            connection.flush()
        delay = float(config.get("delay_seconds", 0))
        if delay:
            time.sleep(delay)
        log("Requested firmware download mode over serial.")
    except Exception as exc:
        if not config.get("best_effort", False):
            raise FlashError(f"Could not request download mode: {exc}") from exc
        log(f"Download-mode command skipped/failed (continuing with ROM reset): {exc}")


def _download_ephemeral(
    target_id: str,
    target: dict[str, Any],
    destination: Path,
    token: CancellationToken,
    progress: ProgressCallback,
    log: LogCallback,
) -> str | None:
    validate_target(target_id, target)
    source = target["source"]
    client = GitHubClient()
    source_path = source["path"].strip("/")
    commit_info = client.latest_commit_for_path(source["repo"], source["branch"], source_path)
    ref = commit_info["sha"] if commit_info else source["branch"]
    commit = commit_info["sha"] if commit_info else None
    remote = {item["name"]: item for item in client.directory(source["repo"], ref, source_path)}
    artifacts = collect_artifacts(target, include_extras=False)
    total_bytes = sum(int(remote.get(item.source_name, {}).get("size", 0)) for item in artifacts)
    completed = 0

    for artifact in artifacts:
        token.raise_if_cancelled()
        info = remote.get(artifact.source_name)
        if not info:
            raise SyncError(f"Missing required firmware file: {artifact.source_name}")
        size = int(info.get("size", 0))
        log(f"Downloading {artifact.source_name} from {source['repo']} @ {ref[:12]}.")

        def report(written: int, _total: int | None, *, base: int = completed, name: str = artifact.local_name) -> None:
            fraction = (base + written) / total_bytes if total_bytes else 0.0
            progress("download", min(1.0, fraction), name)

        client.download_content(
            source["repo"],
            ref,
            f"{source_path}/{artifact.source_name}",
            destination / artifact.local_name,
            progress=report,
            cancelled=lambda: token.cancelled,
        )
        completed += size
    progress("download", 1.0, "Firmware downloaded")
    return commit


_PERCENT_RE = re.compile(r"(?:\(|\b)(\d{1,3})(?:\s*%|\s*percent)", re.IGNORECASE)


def _run_streaming(
    command: list[str],
    token: CancellationToken,
    stage: str,
    progress: ProgressCallback,
    log: LogCallback,
) -> None:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    assert process.stdout is not None
    try:
        for raw in iter(process.stdout.readline, ""):
            if token.cancelled:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                raise OperationCancelled("Operation cancelled by user.")
            line = raw.strip("\r\n")
            if line:
                log(line)
            matches = _PERCENT_RE.findall(line)
            if matches:
                progress(stage, min(1.0, int(matches[-1]) / 100.0), line)
        return_code = process.wait()
    finally:
        process.stdout.close()
    if return_code != 0:
        raise FlashError(f"esptool {stage} failed with code {return_code}; check USB connection and log.")
    progress(stage, 1.0, f"{stage.title()} complete")


def _verify_command(target: dict[str, Any], port: str, base: Path, include_optional: bool) -> list[str]:
    command = _esptool_command() + [
        "--port", port,
        "--baud", str(int(target.get("baud", 460800))),
        "--before", "no-reset",
        "--after", target.get("reset", {}).get("after", "hard-reset"),
        "--chip", target["chip"],
        "verify-flash",
    ]
    for entry in flash_entries(target, include_optional=include_optional):
        command.extend([entry["offset"], str(base / entry["file"])])
    return command


def flash_ephemeral_target(
    target_id: str,
    target: dict[str, Any],
    port: str,
    *,
    confirmed: bool,
    token: CancellationToken | None = None,
    progress: ProgressCallback | None = None,
    log: LogCallback | None = None,
    include_optional: bool = True,
) -> FlashResult:
    if not confirmed:
        raise FlashError("Explicit user confirmation is required before writing flash.")
    token = token or CancellationToken()
    progress = progress or (lambda _stage, _value, _label: None)
    log = log or (lambda _line: None)
    token.raise_if_cancelled()

    device = next((item for item in serial_devices() if item.port == port), None)
    if device and not (device.readable and device.writable):
        raise FlashError(f"No read/write permission for {port}; check dialout/uaccess rules.")

    _send_pre_flash_command(target, port, log)
    detected = detect_esp_chip(port)
    require_compatible_chip(target, detected)
    progress("detect", 1.0, f"Compatible {detected}")

    with tempfile.TemporaryDirectory(prefix=f"ultimate-flasher-{target_id}-") as temp_name:
        base = Path(temp_name)
        commit = _download_ephemeral(target_id, target, base, token, progress, log)
        token.raise_if_cancelled()
        command = build_flash_command(
            target_id,
            target,
            port,
            int(target.get("baud", 460800)),
            flash_mode=target.get("flash_mode", "dio"),
            flash_freq=target.get("flash_freq", "80m"),
            flash_size=target.get("flash_size", "detect"),
            include_optional=include_optional,
            base_dir=base,
        )
        _run_streaming(command, token, "flash", progress, log)
        token.raise_if_cancelled()
        _run_streaming(_verify_command(target, port, base, include_optional), token, "verify", progress, log)
        # TemporaryDirectory removes the binaries only after verification returned successfully.
        return FlashResult(target_id, port, detected, commit)


def usb_host_diagnostics() -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []
    usb_root = Path("/sys/bus/usb/devices")
    checks.append(("USB host controller", usb_root.exists(), str(usb_root)))
    checks.append(("Python architecture", sys.maxsize > 2**32, "64-bit" if sys.maxsize > 2**32 else "32-bit"))
    try:
        import esptool

        checks.append(("esptool", True, getattr(esptool, "__version__", "installed")))
    except ImportError:
        checks.append(("esptool", False, "not installed"))
    try:
        import serial

        checks.append(("pyserial", True, getattr(serial, "VERSION", "installed")))
    except ImportError:
        checks.append(("pyserial", False, "not installed"))
    ports = serial_devices() if checks[-1][1] else []
    checks.append(("USB serial ports", bool(ports), f"{len(ports)} detected"))
    denied = [item.port for item in ports if not (item.readable and item.writable)]
    checks.append(("Serial permissions", not denied, ", ".join(denied) if denied else "read/write OK"))
    return checks
