from __future__ import annotations

import json
import tempfile
import unittest
import warnings
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from zipfile import ZIP_DEFLATED, ZipFile

from scripts.release_gate import finalize_manifest, inspect_release, verify_manifest


def write_assets(
    root: Path,
    *,
    invalid_json: bool = False,
    invalid_utf8: bool = False,
    missing_excel: str | None = None,
    character_record: Any = None,
    missing_enemy_database: bool = False,
    unsafe_entry: bool = False,
) -> tuple[Path, Path, Path]:
    excel = root / "zh_CN-excel.zip"
    levels = root / "zh_CN-levels.zip"
    resource = root / "zh_CN-resource-manifest.zip"
    tables = {
        "character_table.json": {
            "char": character_record
            if character_record is not None
            else {"displayNumber": "A1", "profession": "WARRIOR"}
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
            if name == missing_excel:
                continue
            if invalid_utf8 and name == "zone_table.json":
                content = b"\xff"
            else:
                content = "{" if invalid_json and name == "zone_table.json" else json.dumps(data)
            archive.writestr(f"zh_CN/gamedata/excel/{name}", content)
        if unsafe_entry:
            archive.writestr("../escape.json", "{}")
    with ZipFile(levels, "w", compression=ZIP_DEFLATED) as archive:
        if missing_enemy_database:
            archive.writestr("zh_CN/gamedata/levels/placeholder.json", "{}")
        else:
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

    def test_invalid_utf8_is_reported_separately(self) -> None:
        excel, levels, resource = write_assets(self.root, invalid_utf8=True)
        with self.assertRaisesRegex(ValueError, "invalid UTF-8"):
            finalize_manifest(
                excel_zip=excel,
                levels_zip=levels,
                resource_zip=resource,
                manifest_path=self.manifest,
            )

    def test_unsafe_zip_entry_is_rejected(self) -> None:
        excel, levels, resource = write_assets(self.root, unsafe_entry=True)
        with self.assertRaisesRegex(ValueError, "unsafe zip entry"):
            inspect_release(excel, levels, resource)

    def test_duplicate_zip_entry_is_rejected(self) -> None:
        excel, levels, resource = write_assets(self.root)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with ZipFile(excel, "a") as archive:
                archive.writestr("zh_CN/gamedata/excel/zone_table.json", "{}")
        with self.assertRaisesRegex(ValueError, "duplicate entries"):
            inspect_release(excel, levels, resource)

    def test_missing_required_inputs_are_rejected(self) -> None:
        cases = (
            ({"missing_excel": "item_table.json"}, "missing required entries"),
            ({"missing_enemy_database": True}, "missing .*enemy_database"),
        )
        for index, (options, message) in enumerate(cases):
            with self.subTest(options=options):
                case_root = self.root / f"case-{index}"
                case_root.mkdir()
                excel, levels, resource = write_assets(case_root, **options)
                with self.assertRaisesRegex(ValueError, message):
                    inspect_release(excel, levels, resource)

    def test_non_object_character_entry_is_rejected(self) -> None:
        excel, levels, resource = write_assets(self.root, character_record="invalid")
        with self.assertRaisesRegex(ValueError, "character_table entries"):
            inspect_release(excel, levels, resource)

    def test_minimum_count_is_enforced(self) -> None:
        excel, levels, resource = write_assets(self.root)
        with patch.dict(
            "scripts.release_gate.MINIMUM_COUNTS", {"operators": 2}, clear=True
        ):
            with self.assertRaisesRegex(ValueError, "below safety floor"):
                inspect_release(excel, levels, resource)

    def test_manifest_schema_and_archive_hash_are_verified(self) -> None:
        excel, levels, resource = write_assets(self.root)
        finalized = finalize_manifest(
            excel_zip=excel,
            levels_zip=levels,
            resource_zip=resource,
            manifest_path=self.manifest,
        )
        bad_schema = dict(finalized, schema_version=1)
        self.manifest.write_text(json.dumps(bad_schema), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "schema_version"):
            verify_manifest(
                excel_zip=excel,
                levels_zip=levels,
                resource_zip=resource,
                manifest_path=self.manifest,
            )

        finalized["archives"][excel.name]["sha256"] = "0" * 64
        self.manifest.write_text(json.dumps(finalized), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "does not match asset"):
            verify_manifest(
                excel_zip=excel,
                levels_zip=levels,
                resource_zip=resource,
                manifest_path=self.manifest,
            )

    def test_non_object_manifest_is_rejected(self) -> None:
        excel, levels, resource = write_assets(self.root)
        self.manifest.write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "manifest must be a JSON object"):
            finalize_manifest(
                excel_zip=excel,
                levels_zip=levels,
                resource_zip=resource,
                manifest_path=self.manifest,
            )

    @patch("scripts.release_gate._validated_archive")
    def test_open_archive_is_closed_when_later_open_fails(
        self, validate: MagicMock
    ) -> None:
        first = MagicMock()
        validate.side_effect = [(first, []), ValueError("bad second archive")]
        with self.assertRaisesRegex(ValueError, "bad second archive"):
            inspect_release(Path("first.zip"), Path("second.zip"), Path("third.zip"))
        first.close.assert_called_once_with()

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
