from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_FILE = PROJECT_ROOT / "registry" / "firmware.json"
FIRMWARE_DIR = PROJECT_ROOT / "firmware"
METADATA_FILE = FIRMWARE_DIR / ".metadata.json"
