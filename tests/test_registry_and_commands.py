from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lab5_flasher.flasher import build_flash_command
from lab5_flasher.portable import IncompatibleDevice, SerialDevice, _VERIFY_SUCCESS_RE, _is_expected_post_verify_reset, _normalise_chip, _verify_command, detect_esp_chip, require_compatible_chip, usb_serial_devices
from lab5_flasher.registry import load_registry, validate_target


class RegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = load_registry()

    def test_all_targets_validate(self) -> None:
        for target_id, target in self.registry["targets"].items():
            validate_target(target_id, target)

    def test_monsterrf_exact_original_map(self) -> None:
        target = self.registry["targets"]["monsterrf"]
        self.assertEqual(target["chip"], "esp32c5")
        self.assertEqual(target["flash_size"], "8MB")
        self.assertEqual(
            [(entry["offset"], entry["file"]) for entry in target["flash"]],
            [
                ("0x2000", "bootloader.bin"),
                ("0x10000", "partition-table.bin"),
                ("0x20000", "projectZero.bin"),
                ("0x410000", "projectZero.bin"),
            ],
        )
        self.assertEqual(target["pre_flash"]["serial_command"], "download\n")
        self.assertEqual(target["author"], "elpadrino26 (El Padrino)")

    def test_flash_command_can_use_managed_cache(self) -> None:
        target = self.registry["targets"]["monsterrf"]
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            for entry in target["flash"]:
                (base / entry["file"]).write_bytes(b"test")
            command = build_flash_command(
                "monsterrf",
                target,
                "/dev/ttyACM0",
                460800,
                flash_mode="dio",
                flash_freq="80m",
                flash_size="8MB",
                include_optional=True,
                base_dir=base,
            )
            self.assertIn("write-flash", command)
            self.assertEqual(command.count(str(base / "projectZero.bin")), 2)

    def test_chip_mismatch_is_rejected(self) -> None:
        target = self.registry["targets"]["monsterrf"]
        require_compatible_chip(target, "ESP32-C5")
        require_compatible_chip(target, "ESP32-C5HR8")
        with self.assertRaises(IncompatibleDevice):
            require_compatible_chip(target, "ESP32-S3")

    def test_verify_uses_the_same_flash_header_options(self) -> None:
        target = self.registry["targets"]["monsterrf"]
        command = _verify_command(target, "/dev/ttyACM0", Path("/cache"), True)
        self.assertEqual(command[command.index("--flash-mode") + 1], "dio")
        self.assertEqual(command[command.index("--flash-freq") + 1], "80m")
        self.assertEqual(command[command.index("--flash-size") + 1], "8MB")

    def test_current_esptool_chip_name_normalises_to_registry_id(self) -> None:
        self.assertEqual(_normalise_chip("ESP32-C5"), "esp32c5")
        with mock.patch("lab5_flasher.portable._run_capture", return_value="Chip type:          ESP32-C5 (QFN40)\n"):
            self.assertEqual(detect_esp_chip("/dev/test"), "esp32c5")

    @mock.patch(
        "lab5_flasher.portable.serial_devices",
        return_value=[
            SerialDevice("/dev/ttyS0", "SoC UART", None, None, True, True),
            SerialDevice("/dev/ttyACM0", "ESP USB", 0x303A, 0x1001, True, True),
        ],
    )
    def test_usb_port_filter_excludes_internal_uart(self, _serial_devices) -> None:
        self.assertEqual([item.port for item in usb_serial_devices()], ["/dev/ttyACM0"])

    def test_esptool_verify_success_markers_are_recognized(self) -> None:
        self.assertIsNotNone(_VERIFY_SUCCESS_RE.search("Hash of data verified."))
        self.assertIsNotNone(_VERIFY_SUCCESS_RE.search("All flash regions verified successfully."))
        self.assertIsNone(_VERIFY_SUCCESS_RE.search("Verification failed at address 0x1000"))
        self.assertTrue(_is_expected_post_verify_reset(2, "verify", True))
        self.assertFalse(_is_expected_post_verify_reset(2, "flash", True))
        self.assertFalse(_is_expected_post_verify_reset(2, "verify", False))
        self.assertFalse(_is_expected_post_verify_reset(1, "verify", True))


if __name__ == "__main__":
    unittest.main()
