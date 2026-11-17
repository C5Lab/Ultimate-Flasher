from __future__ import annotations

import json
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any

from PIL import Image

from lab5_flasher.flipper import FLIPPER_PID, FLIPPER_VID, install_fap_cached
from lab5_flasher.portable import (
    CancellationToken,
    IncompatibleDevice,
    detect_esp_chip,
    flash_cached_target,
    request_download_mode,
    require_compatible_chip,
    usb_serial_devices,
)
from lab5_flasher.registry import load_registry

from . import ui
from .platform_io import BACK_KEYS, DOWN_KEYS, KEY_Q, LEFT_KEYS, Keyboard, OK_KEYS, RIGHT_KEYS, Screen, UP_KEYS, set_brightness


GROUPS = ("LAB5 GEAR", "M5 DEVICES", "FLIPPER ZERO", "SETTINGS")
SCREEN_TIMEOUTS = (5, 30, 60)
INTRO_SECONDS = 2.5
STAGE_RANGES = {
    "detect": (0.0, 5.0),
    "download": (5.0, 25.0),
    "flash": (25.0, 85.0),
    "install": (25.0, 90.0),
    "verify": (90.0, 100.0),
}


class UltimateFlasherApp:
    def __init__(self) -> None:
        self.registry = load_registry()
        self.screen = "intro"
        self.intro_until = time.monotonic() + INTRO_SECONDS
        self.selected = 0
        self.group = ""
        self.item_id = ""
        self.item: dict[str, Any] = {}
        self.kind = "target"
        self.entries: list[dict[str, Any]] = []
        self.port = ""
        self.detected = ""
        self.progress = 0.0
        self.phase = "idle"
        self.label = ""
        self.result_ok = False
        self.result_text = ""
        self.logs: list[str] = []
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.token: CancellationToken | None = None
        self.worker: threading.Thread | None = None
        self.serial_stop = threading.Event()
        self.last_input = time.monotonic()
        self.screen_blanked = False
        self.settings_path = Path.home() / ".config" / "ultimate-flasher" / "settings.json"
        self.settings = self._load_settings()
        set_brightness(int(self.settings["brightness"]))
        self._set_group_entries()

    def _load_settings(self) -> dict[str, Any]:
        try:
            return {"include_optional": True, "brightness": 80, "screen_timeout": 30, **json.loads(self.settings_path.read_text(encoding="utf-8"))}
        except (OSError, json.JSONDecodeError):
            return {"include_optional": True, "brightness": 80, "screen_timeout": 30}

    def _save_settings(self) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.settings_path.with_suffix(".part")
        temp.write_text(json.dumps(self.settings, indent=2) + "\n", encoding="utf-8")
        temp.replace(self.settings_path)

    def _set_group_entries(self) -> None:
        self.entries = [{"label": name} for name in GROUPS]
        self.selected = min(self.selected, len(self.entries) - 1)

    def _open_group(self, group: str) -> None:
        self.group = group
        self.selected = 0
        if group == "SETTINGS":
            self.screen = "settings"
            self.entries = [
                {"label": "SERIAL"},
                {"label": "SCREEN"},
                {"label": "ABOUT"},
            ]
            return
        self.screen = "items"
        self.entries = []
        if group == "FLIPPER ZERO":
            source = self.registry.get("packages", {})
            self.kind = "package"
        else:
            source = self.registry["targets"]
            self.kind = "target"
        for item_id, item in source.items():
            if item.get("menu_group") != group:
                continue
            self.entries.append(
                {
                    "label": item["name"],
                    "status": "READY" if item.get("enabled", True) else "LOCKED",
                    "id": item_id,
                    "item": item,
                }
            )

    def _open_item(self) -> None:
        entry = self.entries[self.selected]
        self.item_id = entry["id"]
        self.item = entry["item"]
        self.screen = "detail"

    def _ports(self) -> list[dict[str, Any]]:
        entries = []
        for device in usb_serial_devices():
            status = "OK" if device.readable and device.writable else "NO PERM"
            entries.append({"label": device.port, "status": status, "device": device})
        return entries

    def _begin_port_selection(self) -> None:
        self.entries = self._ports()
        self.selected = 0
        if not self.entries:
            self.result_ok = False
            self.result_text = "No USB serial ports found. Check USB Host mode, cable, and power."
            self.screen = "result"
        else:
            self.screen = "ports"

    def _start_probe(self) -> None:
        device = self.entries[self.selected]["device"]
        if not (device.readable and device.writable):
            self.result_ok = False
            self.result_text = f"No read/write permission for {device.port}."
            self.screen = "result"
            return
        self.port = device.port
        self.screen = "operation"
        self.progress, self.phase, self.label = 0.0, "detect", "Checking USB device"
        self.token = CancellationToken()

        def probe() -> None:
            try:
                if self.kind == "package":
                    if device.vid != FLIPPER_VID or device.pid != FLIPPER_PID:
                        raise IncompatibleDevice("This is not a Flipper Zero USB CDC device (0483:5740).")
                    detected = "Flipper Zero"
                else:
                    request_download_mode(self.item, self.port, lambda line: self.events.put(("log", line)))
                    detected = detect_esp_chip(self.port)
                    require_compatible_chip(self.item, detected)
                if self.token and self.token.cancelled:
                    return
                self.events.put(("probe_ok", detected))
            except Exception as exc:
                if not self.token or not self.token.cancelled:
                    self.events.put(("failed", str(exc)))

        self.worker = threading.Thread(target=probe, daemon=True)
        self.worker.start()

    def _start_operation(self) -> None:
        self.screen = "operation"
        self.progress, self.phase, self.label = 0.0, "download", "Checking for updates"
        self.token = CancellationToken()

        def on_progress(stage: str, fraction: float, label: str) -> None:
            start, end = STAGE_RANGES.get(stage, (0.0, 100.0))
            self.events.put(("progress", (start + (end - start) * fraction, stage, label)))

        def on_log(line: str) -> None:
            self.events.put(("log", line))

        def operate() -> None:
            try:
                if self.kind == "package":
                    result = install_fap_cached(
                        self.item_id,
                        self.item,
                        self.port,
                        confirmed=True,
                        token=self.token,
                        progress=on_progress,
                        log=on_log,
                    )
                else:
                    result = flash_cached_target(
                        self.item_id,
                        self.item,
                        self.port,
                        confirmed=True,
                        token=self.token,
                        progress=on_progress,
                        log=on_log,
                        include_optional=bool(self.settings["include_optional"]),
                    )
                self.events.put(("complete", result))
            except Exception as exc:
                self.events.put(("failed", str(exc)))

        self.worker = threading.Thread(target=operate, daemon=True)
        self.worker.start()

    def _settings_action(self) -> None:
        if self.selected == 0:
            self.entries = self._ports()
            self.selected = 0
            if self.entries:
                self.screen = "serial_ports"
            else:
                self.result_ok = False
                self.result_text = "No USB serial device found."
                self.screen = "result"
        elif self.selected == 1:
            self.selected = 0
            self.screen = "screen_settings"
        else:
            self.screen = "about"

    def _start_serial_console(self) -> None:
        self.port = self.entries[self.selected]["device"].port
        self.logs = []
        self.serial_stop.clear()
        self.screen = "serial_console"

        def read_serial() -> None:
            try:
                import serial

                with serial.Serial(self.port, 115200, timeout=0.2) as connection:
                    while not self.serial_stop.is_set():
                        raw = connection.readline()
                        if raw:
                            self.events.put(("serial_line", raw.decode("utf-8", errors="replace").rstrip()))
            except Exception as exc:
                if not self.serial_stop.is_set():
                    self.events.put(("serial_line", f"[serial error] {exc}"))

        self.worker = threading.Thread(target=read_serial, daemon=True)
        self.worker.start()

    def _version(self) -> str:
        try:
            return (Path(__file__).resolve().parent / "VERSION").read_text(encoding="utf-8").strip()
        except OSError:
            return "0.2.0"

    def _drain_events(self) -> None:
        while True:
            try:
                name, payload = self.events.get_nowait()
            except queue.Empty:
                return
            if name == "progress":
                self.progress, self.phase, self.label = payload
            elif name == "log":
                self.logs.append(str(payload))
                self.logs = self.logs[-100:]
            elif name == "serial_line":
                self.logs.append(str(payload))
                self.logs = self.logs[-100:]
            elif name == "probe_ok":
                self.detected = str(payload)
                self.screen = "confirm"
            elif name == "complete":
                self.result_ok = True
                self.result_text = (
                    "Flash verified. Welcome to LAB5 — where hard resets have a soft landing! "
                    "Firmware saved in cache."
                )
                self.screen = "result"
            elif name == "failed":
                self.result_ok = False
                self.result_text = str(payload)
                self.screen = "result"

    def on_key(self, code: int) -> bool:
        self.last_input = time.monotonic()
        if self.screen_blanked:
            self.screen_blanked = False
            set_brightness(int(self.settings["brightness"]))
            return True
        if self.screen == "intro":
            self.screen = "groups"
            return True
        if self.screen == "operation":
            if code in BACK_KEYS and self.token:
                self.token.cancel()
                if self.phase == "detect":
                    self.screen = "detail"
                else:
                    self.label = "Cancelling…"
            return True
        if self.screen == "result":
            if code in OK_KEYS or code in BACK_KEYS:
                self.screen = "groups"
                self.selected = 0
                self._set_group_entries()
            return True
        if self.screen == "serial_console":
            if code in BACK_KEYS:
                self.serial_stop.set()
                self._open_group("SETTINGS")
            return True
        if self.screen == "screen_settings":
            if code in UP_KEYS or code in DOWN_KEYS:
                self.selected = 1 - self.selected
            elif code in LEFT_KEYS or code in RIGHT_KEYS:
                direction = -1 if code in LEFT_KEYS else 1
                if self.selected == 0:
                    self.settings["brightness"] = max(10, min(100, int(self.settings["brightness"]) + direction * 10))
                    set_brightness(int(self.settings["brightness"]))
                else:
                    current = SCREEN_TIMEOUTS.index(int(self.settings["screen_timeout"])) if int(self.settings["screen_timeout"]) in SCREEN_TIMEOUTS else 1
                    self.settings["screen_timeout"] = SCREEN_TIMEOUTS[(current + direction) % len(SCREEN_TIMEOUTS)]
                self._save_settings()
            elif code in BACK_KEYS:
                self._open_group("SETTINGS")
            return True
        if self.screen == "about":
            if code == KEY_Q:
                self.screen = "github_qr"
            elif code in BACK_KEYS:
                self._open_group("SETTINGS")
            return True
        if self.screen == "github_qr":
            if code in BACK_KEYS:
                self.screen = "about"
            return True
        if self.screen == "confirm":
            if code in OK_KEYS:
                self._start_operation()
            elif code in BACK_KEYS:
                self.screen = "detail"
            return True
        if code in UP_KEYS:
            self.selected = (self.selected - 1) % max(1, len(self.entries))
        elif code in DOWN_KEYS:
            self.selected = (self.selected + 1) % max(1, len(self.entries))
        elif code in OK_KEYS:
            if self.screen == "groups":
                self._open_group(GROUPS[self.selected])
            elif self.screen == "items" and self.entries:
                self._open_item()
            elif self.screen == "detail" and self.item.get("enabled", True):
                self._begin_port_selection()
            elif self.screen == "ports":
                self._start_probe()
            elif self.screen == "serial_ports":
                self._start_serial_console()
            elif self.screen == "settings":
                self._settings_action()
        elif code in BACK_KEYS:
            if self.screen == "groups":
                return False
            if self.screen in {"items", "settings"}:
                self.screen = "groups"
                self.selected = 0
                self._set_group_entries()
            elif self.screen == "ports":
                self.screen = "detail"
            elif self.screen == "serial_ports":
                self._open_group("SETTINGS")
            else:
                self._open_group(self.group)
        return True

    def draw(self) -> Image.Image:
        self._drain_events()
        if self.screen == "intro":
            if time.monotonic() < self.intro_until:
                return ui.intro_screen()
            self.screen = "groups"
        if self.screen == "groups":
            return ui.menu("ULTIMATE FLASHER", self.entries, self.selected, "LAB5")
        if self.screen in {"items", "settings", "ports", "serial_ports"}:
            title = "SELECT SERIAL" if self.screen == "serial_ports" else ("SELECT USB" if self.screen == "ports" else self.group)
            return ui.menu(title, self.entries, self.selected)
        if self.screen == "screen_settings":
            return ui.screen_settings(int(self.settings["brightness"]), int(self.settings["screen_timeout"]), self.selected)
        if self.screen == "serial_console":
            return ui.serial_console(self.port, self.logs)
        if self.screen == "about":
            return ui.about(self._version())
        if self.screen == "github_qr":
            return ui.github_qr()
        if self.screen == "detail":
            if self.kind == "package":
                lines = [
                    self.item["name"],
                    "Method: Flipper protobuf RPC",
                    f"Target: {self.item.get('install_path', '-')}",
                    f"Source: {self.item['source']['repo']}",
                    "Verification: on-device MD5",
                ]
            else:
                lines = [
                    self.item["name"],
                    f"Chip: {self.item.get('chip', '-')}",
                    f"Source: {self.item.get('source', {}).get('repo', '-')}",
                    f"Files: {len(self.item.get('flash', []))}",
                    f"Author: {self.item.get('author', 'LAB5 / upstream')}",
                    self.item.get("notes", "Ready to flash."),
                ]
            return ui.detail(self.item["name"], lines, self.item.get("enabled", True))
        if self.screen == "confirm":
            return ui.confirmation(self.item["name"], self.port, self.detected)
        if self.screen == "operation":
            return ui.progress_screen(self.item.get("name", "USB"), self.progress, self.label, self.phase)
        if self.screen == "result":
            return ui.result("ULTIMATE FLASHER", self.result_ok, self.result_text)
        return ui.message("ULTIMATE FLASHER", "STATE ERROR", self.screen, ui.RED)

    def run(self) -> None:
        display = Screen()
        keyboard = Keyboard()
        try:
            running = True
            while running:
                if not self.screen_blanked and time.monotonic() - self.last_input >= int(self.settings["screen_timeout"]):
                    self.screen_blanked = True
                    display.show(Image.new("RGB", (320, 170), "black"))
                    set_brightness(0)
                elif not self.screen_blanked:
                    display.show(self.draw())
                for key in keyboard.read(0.08):
                    if not self.on_key(key):
                        running = False
                        break
        finally:
            if self.token:
                self.token.cancel()
            self.serial_stop.set()
            keyboard.close()
            display.close()


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    app = UltimateFlasherApp()
    if len(argv) == 2 and argv[0] == "--screenshot":
        app.draw().save(argv[1])
        return 0
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
