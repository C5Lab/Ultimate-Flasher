from __future__ import annotations

import unittest

try:
    from CardputerZero import ui
    from CardputerZero.app import UltimateFlasherApp
    from CardputerZero.platform_io import KEY_ENTER
except ImportError:  # Pillow/numpy are optional for the desktop CLI installation.
    ui = None
    UltimateFlasherApp = None


@unittest.skipIf(ui is None, "CardputerZero UI dependencies not installed")
class CardputerUiTests(unittest.TestCase):
    def test_all_core_screens_are_native_resolution(self) -> None:
        screens = [
            ui.intro_screen(),
            ui.menu("Ultimate", [{"label": "LAB5"}], 0),
            ui.detail("MonsterRF", ["ESP32-C5"]),
            ui.confirmation("MonsterRF", "/dev/ttyACM0", "esp32c5"),
            ui.progress_screen("MonsterRF", 42.0, "Writing", "flash", tick=0.0),
            ui.result("Ultimate", True, "Verified"),
        ]
        self.assertTrue(all(image.size == (320, 170) for image in screens))

    def test_supplied_flash_animation_layers_load(self) -> None:
        layers = ui._flash_layers()
        self.assertIsNotNone(layers)
        self.assertTrue(all(layer.size == ui.ANIMATION_SIZE for layer in layers))
        open_frame = ui._render_flash_character(0.0, 0.0)
        connected_frame = ui._render_flash_character(100.0, 0.0)
        self.assertNotEqual(open_frame.tobytes(), connected_frame.tobytes())

    def test_intro_expires_and_can_be_skipped(self) -> None:
        app = UltimateFlasherApp()
        self.assertEqual(app.screen, "intro")
        app.intro_until = 0.0
        app.draw()
        self.assertEqual(app.screen, "groups")

        skipped = UltimateFlasherApp()
        skipped.on_key(KEY_ENTER)
        self.assertEqual(skipped.screen, "groups")


if __name__ == "__main__":
    unittest.main()
