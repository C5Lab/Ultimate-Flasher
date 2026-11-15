from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .github_sync import target_dir
from .registry import flash_entries


class FlashError(RuntimeError):
    pass


RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"


def _serial_modules():
    try:
        import serial
        import serial.tools.list_ports
    except ImportError as exc:
        raise FlashError("Missing pyserial. Run through: python ultimate.py ...") from exc
    return serial


def list_ports() -> list[str]:
    _serial_modules()
    import serial.tools.list_ports

    return [port.device for port in serial.tools.list_ports.comports()]


def wait_for_new_port(before: set[str], timeout: float = 20.0) -> str:
    print(f"{CYAN}Hold BOOT and connect the board to enter ROM mode.{RESET}")
    print(f"{YELLOW}Waiting for new serial port...{RESET}")
    spinner = ["|", "/", "-", "\\"]
    started = time.time()
    i = 0
    while time.time() - started < timeout:
        after = set(list_ports())
        new_ports = after - before
        sys.stdout.write(f"\r{spinner[i % len(spinner)]} ")
        sys.stdout.flush()
        i += 1
        if new_ports:
            sys.stdout.write("\r")
            sys.stdout.flush()
            return sorted(new_ports)[0]
        time.sleep(0.15)
    raise FlashError("No new serial port detected.")


def choose_port(port: str | None, timeout: float) -> str:
    if port:
        return port
    before = set(list_ports())
    return wait_for_new_port(before, timeout=timeout)


def ensure_esptool() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "esptool", "version"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise FlashError("Missing esptool. Run through: python ultimate.py ...")


def _reset_option(target: dict[str, Any], key: str, default: str) -> str:
    return target.get("reset", {}).get(key, default)


def erase_flash(target: dict[str, Any], port: str, baud: int) -> None:
    ensure_esptool()
    before = _reset_option(target, "before", "default-reset")
    after = _reset_option(target, "erase_after", "no_reset")
    cmd = [
        sys.executable,
        "-m",
        "esptool",
        "-p",
        port,
        "-b",
        str(baud),
        "--before",
        before,
        "--after",
        after,
        "--chip",
        target["chip"],
        "erase_flash",
    ]
    print(f"{CYAN}Erasing full flash:{RESET} {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise FlashError(f"Erase failed with code {result.returncode}.")


def build_flash_command(
    target_id: str,
    target: dict[str, Any],
    port: str,
    baud: int,
    *,
    flash_mode: str,
    flash_freq: str,
    flash_size: str,
    include_optional: bool,
    base_dir: Path | None = None,
) -> list[str]:
    base = base_dir or target_dir(target_id)
    entries = flash_entries(target, include_optional=include_optional)
    if not entries:
        raise FlashError(f"Target '{target_id}' has no flash entries.")

    before = _reset_option(target, "before", "default-reset")
    after = _reset_option(target, "after", "no_reset")
    cmd = [
        sys.executable,
        "-m",
        "esptool",
        "-p",
        port,
        "-b",
        str(baud),
        "--before",
        before,
        "--after",
        after,
        "--chip",
        target["chip"],
        "write-flash",
        "--flash-mode",
        flash_mode,
        "--flash-freq",
        flash_freq,
        "--flash-size",
        flash_size,
    ]

    missing = []
    for entry in entries:
        path = base / entry["file"]
        if not path.exists():
            missing.append(str(path))
            continue
        cmd.extend([entry["offset"], str(path)])
    if missing:
        raise FlashError("Missing firmware files:\n  " + "\n  ".join(missing))
    return cmd


def flash_target(
    target_id: str,
    target: dict[str, Any],
    *,
    port: str,
    baud: int,
    flash_mode: str,
    flash_freq: str,
    flash_size: str,
    include_optional: bool,
) -> None:
    ensure_esptool()
    cmd = build_flash_command(
        target_id,
        target,
        port,
        baud,
        flash_mode=flash_mode,
        flash_freq=flash_freq,
        flash_size=flash_size,
        include_optional=include_optional,
    )
    print(f"{CYAN}Flashing command:{RESET} {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise FlashError(f"Flash failed with code {result.returncode}.")


def _pulse(ser: Any, dtr: bool | None = None, rts: bool | None = None, delay: float = 0.06) -> None:
    if dtr is not None:
        ser.dtr = dtr
    if rts is not None:
        ser.rts = rts
    time.sleep(delay)


def reset_to_app(port: str) -> None:
    serial = _serial_modules()
    print(f"{YELLOW}Issuing post-flash reset (RTS/DTR) to run app...{RESET}")
    try:
        with serial.Serial(port, 115200, timeout=0.1) as ser:
            _pulse(ser, dtr=False, rts=None)
            _pulse(ser, rts=True)
            _pulse(ser, rts=False)
        print(f"{GREEN}Reset sent. If not, press the board RESET button manually.{RESET}")
    except Exception as exc:
        print(f"{RED}RTS/DTR reset failed: {exc}{RESET}")
        print(f"{YELLOW}Press the board RESET button manually.{RESET}")


def monitor(port: str, baud: int) -> None:
    serial = _serial_modules()
    print(f"{CYAN}Opening serial monitor on {port} @ {baud} (Ctrl+C to exit)...{RESET}")
    try:
        time.sleep(0.3)
        with serial.Serial(port, baud, timeout=0.2) as ser:
            while True:
                try:
                    data = ser.read(1024)
                    if data:
                        sys.stdout.write(data.decode(errors="replace"))
                        sys.stdout.flush()
                except KeyboardInterrupt:
                    break
    except Exception as exc:
        raise FlashError(f"Monitor failed: {exc}") from exc


def firmware_dir_for_display(target_id: str) -> Path:
    return target_dir(target_id)
