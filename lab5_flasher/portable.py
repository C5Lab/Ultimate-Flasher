from __future__ import annotations

import os
import re
import queue
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .flasher import FlashError, build_flash_command
from .registry import flash_entries


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


def usb_serial_devices() -> list[SerialDevice]:
    """Return only serial ports backed by a USB device, never SoC UARTs like ttyS0."""
    return [item for item in serial_devices() if item.vid is not None and item.pid is not None]


def _normalise_chip(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


_ESP_CHIP_FAMILIES = (
    "esp32p4",
    "esp32c6",
    "esp32c5",
    "esp32c3",
    "esp32c2",
    "esp32h4",
    "esp32h2",
    "esp32s3",
    "esp32s2",
    "esp32",
)


def _canonical_chip(value: str) -> str:
    """Reduce esptool package/memory variants (for example C5HR8) to a chip family."""
    normalised = _normalise_chip(value)
    for family in _ESP_CHIP_FAMILIES:
        if normalised.startswith(family):
            return family
    return normalised


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
        r"Chip type:\s+(ESP32[-A-Za-z0-9]+)",
        r"Chip is\s+(ESP32[-A-Za-z0-9]+)",
        r"Detecting chip type\.\.\.\s*(ESP32[-A-Za-z0-9]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, output, flags=re.IGNORECASE)
        if match:
            return _normalise_chip(match.group(1))
    raise FlashError("esptool connected, but did not report a recognizable chip type.")


def require_compatible_chip(target: dict[str, Any], detected_chip: str) -> None:
    expected = _canonical_chip(str(target["chip"]))
    actual = _canonical_chip(detected_chip)
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


def request_download_mode(target: dict[str, Any], port: str, log: LogCallback | None = None) -> None:
    """Run a registry-declared, non-writing transition into ROM download mode."""
    _send_pre_flash_command(target, port, log or (lambda _line: None))


_PERCENT_RE = re.compile(r"(?:\(|\b)(\d{1,3})(?:\s*%|\s*percent)", re.IGNORECASE)
_WRITE_ADDRESS_RE = re.compile(r"Writing at\s+(0x[0-9a-fA-F]+)")
_VERIFY_SUCCESS_RE = re.compile(
    r"(?:hash of data verified|verified successfully|flash verified)",
    re.IGNORECASE,
)


def _is_expected_post_verify_reset(return_code: int | None, stage: str, verified: bool) -> bool:
    return return_code == 2 and stage == "verify" and verified


def _run_streaming(
    command: list[str],
    token: CancellationToken,
    stage: str,
    progress: ProgressCallback,
    log: LogCallback,
    regions: list[tuple[int, int]] | None = None,
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
    output_queue: queue.Queue[str | None] = queue.Queue()

    def read_output() -> None:
        assert process.stdout is not None
        try:
            for output_line in iter(process.stdout.readline, ""):
                output_queue.put(output_line)
        finally:
            output_queue.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    return_code: int | None = None
    verification_succeeded = False
    try:
        stream_finished = False
        while not stream_finished:
            if token.cancelled:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                raise OperationCancelled("Operation cancelled by user.")
            try:
                raw = output_queue.get(timeout=0.1)
            except queue.Empty:
                if process.poll() is not None:
                    stream_finished = True
                continue
            if raw is None:
                stream_finished = True
                continue
            line = raw.strip("\r\n")
            if line:
                log(line)
                if stage == "verify" and _VERIFY_SUCCESS_RE.search(line):
                    verification_succeeded = True
            matches = _PERCENT_RE.findall(line)
            address_match = _WRITE_ADDRESS_RE.search(line)
            if address_match and regions:
                address = int(address_match.group(1), 16)
                total_size = sum(size for _, size in regions)
                completed_before = 0
                for start, size in regions:
                    if start <= address <= start + size + 4096:
                        written = max(0, min(size, address - start))
                        fraction = (completed_before + written) / total_size if total_size else 0.0
                        progress(stage, min(1.0, fraction), line)
                        break
                    completed_before += size
            elif matches:
                progress(stage, min(1.0, int(matches[-1]) / 100.0), line)
        return_code = process.wait()
    finally:
        process.stdout.close()
    if _is_expected_post_verify_reset(return_code, stage, verification_succeeded):
        log("Verification completed; ignoring expected code 2 after hard reset.")
    elif return_code != 0:
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
        "--flash-mode", target.get("flash_mode", "dio"),
        "--flash-freq", target.get("flash_freq", "80m"),
        "--flash-size", target.get("flash_size", "detect"),
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
    cache_root: Path | None = None,
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

    from .device_cache import prepare_target_cache

    prepared = prepare_target_cache(target_id, target, token, progress, log, cache_root)
    base = prepared.directory
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
    regions = [
        (int(str(entry["offset"]), 0), (base / entry["file"]).stat().st_size)
        for entry in flash_entries(target, include_optional=include_optional)
    ]
    _run_streaming(command, token, "flash", progress, log, regions=regions)
    token.raise_if_cancelled()
    _run_streaming(_verify_command(target, port, base, include_optional), token, "verify", progress, log)
    return FlashResult(target_id, port, detected, prepared.commit)


# Kept as the public name used by the CardputerZero UI; artifacts are now cached and updated atomically.
flash_cached_target = flash_ephemeral_target


def usb_host_diagnostics() -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []
    usb_root = Path("/sys/bus/usb/devices")
    checks.append(("USB host controller", usb_root.exists(), str(usb_root)))
    role_files = list(Path("/sys/class/usb_role").glob("*/role"))
    if role_files:
        roles = []
        for role_file in role_files:
            try:
                roles.append(role_file.read_text(encoding="utf-8").strip().lower())
            except OSError:
                roles.append("unreadable")
        checks.append(("USB role", "host" in roles, ", ".join(roles)))
    else:
        checks.append(
            (
                "USB role",
                usb_root.exists(),
                "role sysfs unavailable; verify the physical Host switch",
            )
        )
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
    ports = usb_serial_devices() if checks[-1][1] else []
    checks.append(("USB serial ports", bool(ports), f"{len(ports)} detected"))
    denied = [item.port for item in ports if not (item.readable and item.writable)]
    checks.append(("Serial permissions", not denied, ", ".join(denied) if denied else "read/write OK"))
    return checks
