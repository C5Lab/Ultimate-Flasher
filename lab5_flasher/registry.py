from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import REGISTRY_FILE


class RegistryError(RuntimeError):
    pass


@dataclass(frozen=True)
class Artifact:
    local_name: str
    source_name: str
    required: bool = True
    role: str = "artifact"


def load_registry(path: Path = REGISTRY_FILE) -> dict[str, Any]:
    if not path.exists():
        raise RegistryError(f"Registry file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RegistryError(f"Invalid registry JSON: {exc}") from exc
    if data.get("schema") != 1:
        raise RegistryError("Unsupported registry schema. Expected schema=1.")
    if not isinstance(data.get("targets"), dict):
        raise RegistryError("Registry must contain a 'targets' object.")
    return data


def target_items(registry: dict[str, Any], include_disabled: bool = False) -> list[tuple[str, dict[str, Any]]]:
    items: list[tuple[str, dict[str, Any]]] = []
    for target_id, target in registry["targets"].items():
        if include_disabled or target.get("enabled", True):
            items.append((target_id, target))
    return items


def package_items(registry: dict[str, Any], include_disabled: bool = False) -> list[tuple[str, dict[str, Any]]]:
    items: list[tuple[str, dict[str, Any]]] = []
    for package_id, package in registry.get("packages", {}).items():
        if include_disabled or package.get("enabled", True):
            items.append((package_id, package))
    return items


def get_target(registry: dict[str, Any], target_id: str, include_disabled: bool = False) -> dict[str, Any]:
    targets = registry["targets"]
    if target_id not in targets:
        available = ", ".join(sorted(targets))
        raise RegistryError(f"Unknown target '{target_id}'. Available targets: {available}")
    target = targets[target_id]
    if not include_disabled and not target.get("enabled", True):
        raise RegistryError(f"Target '{target_id}' is disabled in registry/firmware.json.")
    return target


def validate_target(target_id: str, target: dict[str, Any]) -> None:
    required_fields = ["name", "chip", "source", "flash"]
    missing = [field for field in required_fields if field not in target]
    if missing:
        raise RegistryError(f"Target '{target_id}' is missing fields: {', '.join(missing)}")
    source = target["source"]
    if source.get("type") != "github":
        raise RegistryError(f"Target '{target_id}' has unsupported source type: {source.get('type')}")
    for field in ["repo", "branch", "path"]:
        if not source.get(field):
            raise RegistryError(f"Target '{target_id}' source is missing '{field}'.")
    for entry in target.get("flash", []):
        if not entry.get("offset") or not entry.get("file"):
            raise RegistryError(f"Target '{target_id}' has an invalid flash entry: {entry}")


def validate_package(package_id: str, package: dict[str, Any]) -> None:
    required_fields = ["name", "source", "files"]
    missing = [field for field in required_fields if field not in package]
    if missing:
        raise RegistryError(f"Package '{package_id}' is missing fields: {', '.join(missing)}")
    source = package["source"]
    if source.get("type") != "github":
        raise RegistryError(f"Package '{package_id}' has unsupported source type: {source.get('type')}")
    for field in ["repo", "branch", "path"]:
        if not source.get(field):
            raise RegistryError(f"Package '{package_id}' source is missing '{field}'.")
    for entry in package.get("files", []):
        if not entry.get("file"):
            raise RegistryError(f"Package '{package_id}' has an invalid file entry: {entry}")


def collect_artifacts(target: dict[str, Any], include_extras: bool = True) -> list[Artifact]:
    artifacts: dict[str, Artifact] = {}
    for entry in target.get("flash", []):
        local_name = entry["file"]
        source_name = entry.get("source_file", local_name)
        artifacts.setdefault(local_name, Artifact(local_name, source_name, True, "flash"))

    if include_extras:
        for extra in target.get("extras", []):
            if isinstance(extra, str):
                local_name = extra
                source_name = extra
                required = False
            else:
                local_name = extra["file"]
                source_name = extra.get("source_file", local_name)
                required = bool(extra.get("required", False))
            artifacts.setdefault(local_name, Artifact(local_name, source_name, required, "extra"))

    return list(artifacts.values())


def flash_entries(target: dict[str, Any], include_optional: bool = True) -> list[dict[str, Any]]:
    entries = []
    for entry in target.get("flash", []):
        if entry.get("optional") and not include_optional:
            continue
        if entry.get("optional") and entry.get("enabled_by_default") is False and include_optional:
            continue
        entries.append(entry)
    return entries


def choose_target_interactive(registry: dict[str, Any]) -> str:
    items = target_items(registry)
    if not items:
        raise RegistryError("No enabled targets in registry.")
    if not sys.stdin.isatty():
        raise RegistryError("Target is required in non-interactive mode.")

    print("Select firmware target:")
    for index, (target_id, target) in enumerate(items, start=1):
        print(f"  {index}) {target['name']} [{target_id}]")
    while True:
        choice = input("> ").strip()
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(items):
                return items[idx - 1][0]
        if choice in registry["targets"] and registry["targets"][choice].get("enabled", True):
            return choice
        print("Invalid choice.")
