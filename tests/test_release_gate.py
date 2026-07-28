from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from scripts.release_gate import finalize_manifest, verify_manifest


def write_assets(root: Path, *, invalid_json: bool = False) -> tuple[Path, Path, Path]:
    excel = root / "zh_CN-excel.zip"
    levels = root / "zh_CN-levels.zip"
    resource = root / "zh_CN-resource-manifest.zip"
    tables = {
        "character_table.json": {
            "char": {"displayNumber": "A1", "profession": "WARRIOR"}
        },
        "handbook_info_table.json": {},
        "charword_table.json": {},
        "story_review_table.json": {"event": {}},
        "enemy_handbook_table.json": {"enemyData": {"enemy": {}}},
        "stage_table.json": {"stages": {"stage": {}}},
        "zone_table.json": {},
        "item_table.json": {"items": {"item": {}}},
    }
    with ZipFile(excel, "w", compression=ZIP_DEFLATED) as archive:
        for name, data in tables.items():
            content = "{" if invalid_json and name == "zone_table.json" else json.dumps(data)
            archive.writestr(f"zh_CN/gamedata/excel/{name}", content)
    with ZipFile(levels, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "zh_CN/gamedata/levels/enemydata/enemy_database.json",
            json.dumps({"enemies": [{}]}),
        )
    with ZipFile(resource, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("zh_CN/resource_manifest_idx.json", "{}")
    return excel, levels, resource


class ReleaseGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(
            '{"source":"Kengxxiao/ArknightsGameData","upstream_commit":"abc"}\n',
            encoding="utf-8",
        )
        self.minimums = patch(
            "scripts.release_gate.MINIMUM_COUNTS",
            {
                "character_records": 1,
                "operators": 1,
                "items": 1,
                "stages": 1,
                "handbook_enemies": 1,
                "story_events": 1,
                "battle_enemies": 1,
                "excel_json_files": 1,
                "level_json_files": 1,
            },
        )
        self.minimums.start()

    def tearDown(self) -> None:
        self.minimums.stop()
        self.tempdir.cleanup()

    def test_finalize_and_verify_assets(self) -> None:
        excel, levels, resource = write_assets(self.root)
        manifest = finalize_manifest(
            excel_zip=excel,
            levels_zip=levels,
            resource_zip=resource,
            manifest_path=self.manifest,
        )
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["metrics"]["character_records"], 1)
        self.assertEqual(manifest["metrics"]["operators"], 1)
        verify_manifest(
            excel_zip=excel,
            levels_zip=levels,
            resource_zip=resource,
            manifest_path=self.manifest,
        )

        with ZipFile(excel, "a") as archive:
            archive.writestr("zh_CN/gamedata/excel/extra.json", "{}")
        with self.assertRaisesRegex(ValueError, "metrics|record"):
            verify_manifest(
                excel_zip=excel,
                levels_zip=levels,
                resource_zip=resource,
                manifest_path=self.manifest,
            )

    def test_invalid_json_is_rejected(self) -> None:
        excel, levels, resource = write_assets(self.root, invalid_json=True)
        with self.assertRaisesRegex(ValueError, "invalid JSON"):
            finalize_manifest(
                excel_zip=excel,
                levels_zip=levels,
                resource_zip=resource,
                manifest_path=self.manifest,
            )

    def test_large_regression_is_rejected(self) -> None:
        excel, levels, resource = write_assets(self.root)
        previous = self.root / "previous.json"
        previous.write_text(
            json.dumps({"metrics": {"operators": 10}}), encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "operators regressed"):
            finalize_manifest(
                excel_zip=excel,
                levels_zip=levels,
                resource_zip=resource,
                manifest_path=self.manifest,
                previous_manifest=previous,
            )


if __name__ == "__main__":
    unittest.main()
