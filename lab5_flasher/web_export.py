from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .github_sync import package_dir, required_flash_files_exist, target_dir
from .paths import PROJECT_ROOT
from .registry import (
    RegistryError,
    flash_entries,
    package_items,
    target_items,
    validate_package,
    validate_target,
)


WEB_DIR = PROJECT_ROOT / "web"
GENERATED_DIR = WEB_DIR / "generated"
WEB_FIRMWARE_DIR = GENERATED_DIR / "firmware"
MANIFEST_DIR = GENERATED_DIR / "manifests"


CHIP_FAMILY_BY_ESPTOOL_CHIP = {
    "esp8266": "ESP8266",
    "esp32": "ESP32",
    "esp32c2": "ESP32-C2",
    "esp32c3": "ESP32-C3",
    "esp32c5": "ESP32-C5",
    "esp32c6": "ESP32-C6",
    "esp32c61": "ESP32-C61",
    "esp32h2": "ESP32-H2",
    "esp32p4": "ESP32-P4",
    "esp32s2": "ESP32-S2",
    "esp32s3": "ESP32-S3",
}


class WebExportError(RuntimeError):
    pass


def _chip_family(target: dict[str, Any]) -> str:
    web = target.get("web", {})
    if web.get("chip_family"):
        return web["chip_family"]
    chip = target["chip"].lower()
    try:
        return CHIP_FAMILY_BY_ESPTOOL_CHIP[chip]
    except KeyError as exc:
        raise WebExportError(f"Unsupported chip family for web export: {chip}") from exc


def _offset_to_int(offset: str) -> int:
    return int(str(offset), 0)


def _target_web_dir(target_id: str) -> Path:
    return WEB_FIRMWARE_DIR / target_id


def _copy_target_files(target_id: str, target: dict[str, Any]) -> list[dict[str, Any]]:
    source_dir = target_dir(target_id)
    destination_dir = _target_web_dir(target_id)
    destination_dir.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    copied: list[dict[str, Any]] = []
    for entry in flash_entries(target, include_optional=True):
        file_name = entry["file"]
        if file_name in seen:
            continue
        seen.add(file_name)
        source = source_dir / file_name
        if not source.exists():
            raise WebExportError(f"Missing cached firmware file for web export: {source}")
        destination = destination_dir / file_name
        shutil.copy2(source, destination)
        copied.append(
            {
                "file": file_name,
                "path": f"generated/firmware/{target_id}/{file_name}",
                "size": destination.stat().st_size,
            }
        )
    return copied


def _web_tools_manifest(target_id: str, target: dict[str, Any]) -> dict[str, Any]:
    parts = []
    for entry in flash_entries(target, include_optional=True):
        parts.append(
            {
                "path": f"../firmware/{target_id}/{entry['file']}",
                "offset": _offset_to_int(entry["offset"]),
            }
        )
    return {
        "name": target["name"],
        "version": target.get("version", "latest"),
        "builds": [
            {
                "chipFamily": _chip_family(target),
                "parts": parts,
            }
        ],
    }


def export_web(registry: dict[str, Any], *, target_id: str | None = None) -> list[str]:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    WEB_FIRMWARE_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)

    if target_id:
        items = [(target_id, registry["targets"][target_id])]
    else:
        items = target_items(registry)

    exported_targets: list[dict[str, Any]] = []
    exported_packages: list[dict[str, Any]] = []
    exported_ids: list[str] = []
    for current_id, target in items:
        if not target.get("enabled", True):
            continue
        validate_target(current_id, target)
        if not required_flash_files_exist(current_id, target):
            raise WebExportError(
                f"Target '{current_id}' is not synced. Run: python ultimate.py sync {current_id} --no-extras"
            )

        copied = _copy_target_files(current_id, target)
        manifest = _web_tools_manifest(current_id, target)
        manifest_path = MANIFEST_DIR / f"{current_id}.json"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

        exported_targets.append(
            {
                "id": current_id,
                "name": target["name"],
                "family": target.get("family", ""),
                "chip": target["chip"],
                "chip_family": _chip_family(target),
                "baud": target.get("baud", 460800),
                "manifest": f"generated/manifests/{current_id}.json",
                "firmware_files": copied,
                "flash": [
                    {
                        "name": entry.get("name", entry["file"]),
                        "offset": entry["offset"],
                        "file": entry["file"],
                        "optional": bool(entry.get("optional", False)),
                    }
                    for entry in flash_entries(target, include_optional=True)
                ],
                "usb": target.get("web", {}).get("usb", []),
            }
        )
        exported_ids.append(current_id)

    if target_id is None:
        for package_id, package in package_items(registry):
            validate_package(package_id, package)
            destination_dir = WEB_FIRMWARE_DIR / "packages" / package_id
            destination_dir.mkdir(parents=True, exist_ok=True)
            files = []
            for entry in package.get("files", []):
                source = package_dir(package_id) / entry["file"]
                if not source.exists():
                    raise WebExportError(
                        f"Package '{package_id}' is not synced. Run: python ultimate.py sync"
                    )
                destination = destination_dir / entry["file"]
                shutil.copy2(source, destination)
                files.append(
                    {
                        "file": entry["file"],
                        "label": entry.get("label", entry["file"]),
                        "path": f"generated/firmware/packages/{package_id}/{entry['file']}",
                        "size": destination.stat().st_size,
                    }
                )
            exported_packages.append(
                {
                    "id": package_id,
                    "name": package["name"],
                    "family": package.get("family", ""),
                    "kind": package.get("kind", "download"),
                    "notes": package.get("notes", ""),
                    "files": files,
                }
            )

    index = {
        "schema": 1,
        "title": "Ultimate LAB5 Web Flasher",
        "targets": exported_targets,
        "packages": exported_packages,
    }
    (GENERATED_DIR / "targets.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return exported_ids
