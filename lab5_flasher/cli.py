from __future__ import annotations

import argparse
import sys
from typing import Sequence

from . import __version__
from .flasher import (
    FlashError,
    choose_port,
    erase_flash,
    firmware_dir_for_display,
    flash_target,
    list_ports,
    monitor,
    reset_to_app,
)
from .github_sync import (
    SyncError,
    describe_cached_package,
    describe_cached_target,
    required_flash_files_exist,
    sync_package,
    sync_target,
)
from .registry import (
    RegistryError,
    choose_target_interactive,
    get_target,
    load_registry,
    package_items,
    target_items,
)
from .web_export import WebExportError, export_web


def _print_sync_result(result) -> None:
    if result.message == "disabled":
        print(f"- {result.target_id}: skipped (disabled)")
        return
    commit = f" @ {result.commit[:12]}" if result.commit else ""
    print(
        f"- {result.target_id}{commit}: checked={result.checked}, "
        f"downloaded={result.downloaded}, unchanged={result.unchanged}, skipped={result.skipped}"
    )


def command_list(args: argparse.Namespace) -> int:
    registry = load_registry()
    for target_id, target in target_items(registry, include_disabled=args.include_disabled):
        enabled = "enabled" if target.get("enabled", True) else "disabled"
        print(f"{target_id:18} {enabled:8} {target.get('chip', '-'):8} {target['name']}")
    return 0


def command_ports(_args: argparse.Namespace) -> int:
    ports = list_ports()
    if not ports:
        print("No serial ports detected.")
        return 0
    for port in ports:
        print(port)
    return 0


def command_sync(args: argparse.Namespace) -> int:
    registry = load_registry()
    if args.clean:
        from .github_sync import clean_target_cache

        target_id = args.target
        if not target_id:
            raise RegistryError("--clean requires a target.")
        clean_target_cache(target_id)
        print(f"Cleaned cache for {target_id}.")
        return 0

    targets: list[tuple[str, dict]]
    if args.target:
        target = get_target(registry, args.target, include_disabled=args.include_disabled)
        targets = [(args.target, target)]
    else:
        targets = target_items(registry, include_disabled=args.include_disabled)

    failed = False
    for target_id, target in targets:
        try:
            result = sync_target(
                target_id,
                target,
                dry_run=args.dry_run,
                force=args.force,
                include_extras=not args.no_extras,
            )
            _print_sync_result(result)
        except (SyncError, RegistryError) as exc:
            failed = True
            print(f"- {target_id}: ERROR: {exc}", file=sys.stderr)
    if not args.target:
        for package_id, package in package_items(registry, include_disabled=args.include_disabled):
            try:
                result = sync_package(package_id, package, dry_run=args.dry_run, force=args.force)
                _print_sync_result(result)
            except (SyncError, RegistryError) as exc:
                failed = True
                print(f"- {package_id}: ERROR: {exc}", file=sys.stderr)
    return 1 if failed else 0


def command_status(args: argparse.Namespace) -> int:
    registry = load_registry()
    items = [(args.target, get_target(registry, args.target, include_disabled=True))] if args.target else target_items(
        registry, include_disabled=True
    )
    for target_id, target in items:
        cached = describe_cached_target(target_id)
        state = "cached" if cached else "not cached"
        enabled = "enabled" if target.get("enabled", True) else "disabled"
        print(f"{target_id}: {state}, {enabled}")
        if cached:
            commit = cached.get("source_commit") or "-"
            synced_at = cached.get("synced_at") or "-"
            print(f"  source: {cached.get('source_repo')}/{cached.get('source_path')} @ {commit}")
            print(f"  synced: {synced_at}")
            for name, info in sorted(cached.get("files", {}).items()):
                print(f"  - {name}: {info.get('size', '?')} bytes sha256={info.get('sha256', '-')[:16]}")
        else:
            print(f"  firmware dir: {firmware_dir_for_display(target_id)}")
    if not args.target:
        for package_id, package in package_items(registry, include_disabled=True):
            cached = describe_cached_package(package_id)
            state = "cached" if cached else "not cached"
            enabled = "enabled" if package.get("enabled", True) else "disabled"
            print(f"{package_id}: {state}, {enabled}, package")
            if cached:
                commit = cached.get("source_commit") or "-"
                synced_at = cached.get("synced_at") or "-"
                print(f"  source: {cached.get('source_repo')}/{cached.get('source_path')} @ {commit}")
                print(f"  synced: {synced_at}")
                for name, info in sorted(cached.get("files", {}).items()):
                    print(f"  - {name}: {info.get('size', '?')} bytes sha256={info.get('sha256', '-')[:16]}")
    return 0


def command_doctor(_args: argparse.Namespace) -> int:
    print(f"Ultimate LAB5 Flasher {__version__}")
    print(f"Python: {sys.version.split()[0]}")
    try:
        import serial

        print(f"pyserial: {serial.VERSION}")
    except Exception as exc:
        print(f"pyserial: missing ({exc})")
    try:
        import esptool

        version = getattr(esptool, "__version__", "installed")
        print(f"esptool: {version}")
    except Exception as exc:
        print(f"esptool: missing ({exc})")
    return 0


def command_export_web(args: argparse.Namespace) -> int:
    registry = load_registry()
    if args.sync:
        sync_args = argparse.Namespace(
            target=args.target,
            clean=False,
            include_disabled=False,
            dry_run=False,
            force=args.force_sync,
            no_extras=True,
        )
        result = command_sync(sync_args)
        if result != 0:
            if not args.allow_stale:
                return result
            print("WARNING: sync failed, exporting cached web assets because --allow-stale was set.")
    exported = export_web(registry, target_id=args.target)
    for target_id in exported:
        print(f"- exported {target_id}")
    print("Web assets written to web/generated/")
    return 0


def command_flash(args: argparse.Namespace) -> int:
    registry = load_registry()
    target_id = args.target or choose_target_interactive(registry)
    target = get_target(registry, target_id)

    if not args.no_sync:
        try:
            print(f"Checking firmware updates for {target_id}...")
            result = sync_target(target_id, target, force=args.force_sync)
            _print_sync_result(result)
        except (SyncError, RegistryError) as exc:
            if not args.allow_stale or not required_flash_files_exist(target_id, target):
                raise SyncError(
                    f"Automatic sync failed: {exc}\n"
                    "Use --allow-stale only if you intentionally want to flash cached files."
                ) from exc
            print(f"WARNING: sync failed, using cached files because --allow-stale was set: {exc}")

    baud = args.baud or int(target.get("baud", 460800))
    flash_mode = args.flash_mode or target.get("flash_mode", "dio")
    flash_freq = args.flash_freq or target.get("flash_freq", "80m")
    flash_size = args.flash_size or target.get("flash_size", "detect")
    include_optional = not args.skip_optional

    port = choose_port(args.port, timeout=args.wait_timeout)
    print(f"Detected serial port: {port}")
    print("Tip: release the BOOT button before programming finishes.")

    if args.erase:
        erase_flash(target, port, baud)

    flash_target(
        target_id,
        target,
        port=port,
        baud=baud,
        flash_mode=flash_mode,
        flash_freq=flash_freq,
        flash_size=flash_size,
        include_optional=include_optional,
    )

    if not args.no_reset:
        reset_to_app(port)
    if args.monitor:
        monitor(port, baud)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ultimate.py",
        description="Ultimate LAB5 Flasher: sync firmware from source repos and flash ESP devices.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser("list", help="List firmware targets.")
    list_parser.add_argument("--include-disabled", action="store_true")
    list_parser.set_defaults(func=command_list)

    sync_parser = subparsers.add_parser("sync", help="Sync firmware cache from source repositories.")
    sync_parser.add_argument("target", nargs="?", help="Target id. Defaults to all enabled targets.")
    sync_parser.add_argument("--include-disabled", action="store_true")
    sync_parser.add_argument("--force", action="store_true", help="Download even if hashes match.")
    sync_parser.add_argument("--dry-run", action="store_true", help="Check what would change without downloading.")
    sync_parser.add_argument("--no-extras", action="store_true", help="Only sync files used for flashing.")
    sync_parser.add_argument("--clean", action="store_true", help="Remove cached files and metadata for a target.")
    sync_parser.set_defaults(func=command_sync)

    status_parser = subparsers.add_parser("status", help="Show local cache metadata.")
    status_parser.add_argument("target", nargs="?")
    status_parser.set_defaults(func=command_status)

    ports_parser = subparsers.add_parser("ports", help="List serial ports.")
    ports_parser.set_defaults(func=command_ports)

    doctor_parser = subparsers.add_parser("doctor", help="Check local Python flashing dependencies.")
    doctor_parser.set_defaults(func=command_doctor)

    export_web_parser = subparsers.add_parser("export-web", help="Export web manifests and cached firmware.")
    export_web_parser.add_argument("target", nargs="?")
    export_web_parser.add_argument("--sync", action="store_true", help="Sync firmware before exporting.")
    export_web_parser.add_argument("--force-sync", action="store_true", help="Redownload files during --sync.")
    export_web_parser.add_argument("--allow-stale", action="store_true", help="Export cached assets if --sync fails.")
    export_web_parser.set_defaults(func=command_export_web)

    flash_parser = subparsers.add_parser("flash", help="Sync and flash a target.")
    flash_parser.add_argument("target", nargs="?", help="Target id. If omitted, choose interactively.")
    flash_parser.add_argument("--port", help="Known serial port, e.g. COM10 or /dev/ttyACM0.")
    flash_parser.add_argument("--baud", type=int, help="Override target default baud rate.")
    flash_parser.add_argument("--erase", action="store_true", help="Erase flash before writing firmware.")
    flash_parser.add_argument("--monitor", action="store_true", help="Open serial monitor after flashing.")
    flash_parser.add_argument("--no-sync", action="store_true", help="Do not check GitHub before flashing.")
    flash_parser.add_argument("--force-sync", action="store_true", help="Redownload target files before flashing.")
    flash_parser.add_argument("--allow-stale", action="store_true", help="Flash cached files if update check fails.")
    flash_parser.add_argument("--skip-optional", action="store_true", help="Skip optional flash entries.")
    flash_parser.add_argument("--flash-mode", choices=["dio", "qio", "dout", "qout"])
    flash_parser.add_argument("--flash-freq", choices=["80m", "60m", "40m", "26m", "20m"])
    flash_parser.add_argument("--flash-size", default=None)
    flash_parser.add_argument("--wait-timeout", type=float, default=20.0)
    flash_parser.add_argument("--no-reset", action="store_true", help="Do not issue RTS/DTR reset after flashing.")
    flash_parser.set_defaults(func=command_flash)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    try:
        return int(args.func(args))
    except (RegistryError, SyncError, FlashError, WebExportError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
