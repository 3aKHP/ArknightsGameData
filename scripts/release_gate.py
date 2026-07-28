#!/usr/bin/env python3
"""Validate GameData assets and bind them to a release manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import BadZipFile, ZipFile


CONTRACT_VERSION = "prts-gamedata-v1"
MAX_COUNT_DROP_FRACTION = 0.20
EXCEL_ROOT = "zh_CN/gamedata/excel"
LEVELS_ROOT = "zh_CN/gamedata/levels"
REQUIRED_EXCEL = (
    "character_table.json",
    "handbook_info_table.json",
    "charword_table.json",
    "story_review_table.json",
    "enemy_handbook_table.json",
    "stage_table.json",
    "zone_table.json",
    "item_table.json",
)
ENEMY_DATABASE = f"{LEVELS_ROOT}/enemydata/enemy_database.json"
RESOURCE_MANIFEST = "zh_CN/resource_manifest_idx.json"
MINIMUM_COUNTS = {
    "character_records": 800,
    "operators": 250,
    "items": 500,
    "stages": 1000,
    "handbook_enemies": 500,
    "story_events": 100,
    "battle_enemies": 500,
    "excel_json_files": 40,
    "level_json_files": 1000,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(data: bytes, name: str) -> Any:
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON entry {name}: {exc}") from exc


def _validated_archive(path: Path) -> tuple[ZipFile, list[str]]:
    try:
        archive = ZipFile(path)
    except BadZipFile as exc:
        raise ValueError(f"invalid zip {path.name}: {exc}") from exc
    entries = [entry for entry in archive.infolist() if not entry.is_dir()]
    names = [entry.filename for entry in entries]
    if len(names) != len(set(names)):
        archive.close()
        raise ValueError(f"{path.name} contains duplicate entries")
    for name in names:
        item = PurePosixPath(name)
        if item.is_absolute() or ".." in item.parts:
            archive.close()
            raise ValueError(f"unsafe zip entry in {path.name}: {name}")
    return archive, names


def _object_count(value: Any, field: str, name: str) -> int:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    nested = value.get(field)
    if not isinstance(nested, (dict, list)):
        raise ValueError(f"{name}.{field} must be an object or array")
    return len(nested)


def inspect_release(
    excel_zip: Path,
    levels_zip: Path,
    resource_zip: Path,
) -> dict[str, int]:
    """Validate all JSON plus PRTS consumer tables and return release metrics."""
    excel, excel_names = _validated_archive(excel_zip)
    levels, level_names = _validated_archive(levels_zip)
    resource, resource_names = _validated_archive(resource_zip)
    try:
        required = [f"{EXCEL_ROOT}/{name}" for name in REQUIRED_EXCEL]
        missing = [name for name in required if name not in excel_names]
        if missing:
            raise ValueError(f"excel archive missing required entries: {missing}")
        if ENEMY_DATABASE not in level_names:
            raise ValueError(f"levels archive missing {ENEMY_DATABASE}")
        if RESOURCE_MANIFEST not in resource_names:
            raise ValueError(f"resource archive missing {RESOURCE_MANIFEST}")

        excel_data: dict[str, Any] = {}
        for name in excel_names:
            if name.endswith(".json"):
                excel_data[name] = _load_json(excel.read(name), name)
        level_data: dict[str, Any] = {}
        for name in level_names:
            if name.endswith(".json"):
                level_data[name] = _load_json(levels.read(name), name)
        _load_json(resource.read(RESOURCE_MANIFEST), RESOURCE_MANIFEST)

        characters = excel_data[f"{EXCEL_ROOT}/character_table.json"]
        story_review = excel_data[f"{EXCEL_ROOT}/story_review_table.json"]
        if not isinstance(characters, dict) or not isinstance(story_review, dict):
            raise ValueError("character and story review tables must be objects")
        if any(not isinstance(record, dict) for record in characters.values()):
            raise ValueError("character_table entries must be objects")

        metrics = {
            "character_records": len(characters),
            "operators": sum(
                bool(record.get("displayNumber"))
                and record.get("profession") not in {"TOKEN", "TRAP"}
                for record in characters.values()
            ),
            "items": _object_count(
                excel_data[f"{EXCEL_ROOT}/item_table.json"], "items", "item_table"
            ),
            "stages": _object_count(
                excel_data[f"{EXCEL_ROOT}/stage_table.json"], "stages", "stage_table"
            ),
            "handbook_enemies": _object_count(
                excel_data[f"{EXCEL_ROOT}/enemy_handbook_table.json"],
                "enemyData",
                "enemy_handbook_table",
            ),
            "story_events": len(story_review),
            "battle_enemies": _object_count(
                level_data[ENEMY_DATABASE], "enemies", "enemy_database"
            ),
            "excel_json_files": len(excel_data),
            "level_json_files": len(level_data),
        }
    finally:
        excel.close()
        levels.close()
        resource.close()

    for field, minimum in MINIMUM_COUNTS.items():
        if metrics[field] < minimum:
            raise ValueError(f"{field} is {metrics[field]}, below safety floor {minimum}")
    return metrics


def _load_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manifest must be a JSON object")
    return data


def check_regression(metrics: dict[str, int], previous: dict[str, Any] | None) -> None:
    if not previous:
        return
    old_metrics = previous.get("metrics")
    if not isinstance(old_metrics, dict):
        return
    for field, current in metrics.items():
        old = old_metrics.get(field)
        if isinstance(old, int) and old > 0:
            minimum = int(old * (1 - MAX_COUNT_DROP_FRACTION))
            if current < minimum:
                raise ValueError(
                    f"{field} regressed from {old} to {current} (minimum {minimum})"
                )


def _asset_record(path: Path) -> dict[str, int | str]:
    return {"sha256": sha256_file(path), "size_bytes": path.stat().st_size}


def finalize_manifest(
    *,
    excel_zip: Path,
    levels_zip: Path,
    resource_zip: Path,
    manifest_path: Path,
    previous_manifest: Path | None = None,
) -> dict[str, Any]:
    manifest = _load_manifest(manifest_path)
    previous = _load_manifest(previous_manifest) if previous_manifest and previous_manifest.is_file() else None
    metrics = inspect_release(excel_zip, levels_zip, resource_zip)
    check_regression(metrics, previous)
    manifest.update(
        {
            "schema_version": 2,
            "contract_version": CONTRACT_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "archives": {
                excel_zip.name: _asset_record(excel_zip),
                levels_zip.name: _asset_record(levels_zip),
                resource_zip.name: _asset_record(resource_zip),
            },
            "metrics": metrics,
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def verify_manifest(
    *,
    excel_zip: Path,
    levels_zip: Path,
    resource_zip: Path,
    manifest_path: Path,
    previous_manifest: Path | None = None,
) -> None:
    manifest = _load_manifest(manifest_path)
    if manifest.get("schema_version") != 2:
        raise ValueError("manifest schema_version must be 2")
    if manifest.get("contract_version") != CONTRACT_VERSION:
        raise ValueError(f"manifest contract_version must be {CONTRACT_VERSION}")
    metrics = inspect_release(excel_zip, levels_zip, resource_zip)
    if manifest.get("metrics") != metrics:
        raise ValueError("manifest metrics do not match release assets")
    archives = manifest.get("archives")
    if not isinstance(archives, dict):
        raise ValueError("manifest archives must be an object")
    for path in (excel_zip, levels_zip, resource_zip):
        actual = _asset_record(path)
        if archives.get(path.name) != actual:
            raise ValueError(f"manifest record for {path.name} does not match asset")
    previous = _load_manifest(previous_manifest) if previous_manifest and previous_manifest.is_file() else None
    check_regression(metrics, previous)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("finalize", "verify"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--excel", type=Path, required=True)
        subparser.add_argument("--levels", type=Path, required=True)
        subparser.add_argument("--resource", type=Path, required=True)
        subparser.add_argument("--manifest", type=Path, required=True)
        subparser.add_argument("--previous-manifest", type=Path)
    args = parser.parse_args()
    kwargs = {
        "excel_zip": args.excel,
        "levels_zip": args.levels,
        "resource_zip": args.resource,
        "manifest_path": args.manifest,
        "previous_manifest": args.previous_manifest,
    }
    if args.command == "finalize":
        finalize_manifest(**kwargs)
    else:
        verify_manifest(**kwargs)


if __name__ == "__main__":
    main()
