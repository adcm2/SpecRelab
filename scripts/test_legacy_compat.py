#!/usr/bin/env python3
"""Focused adversarial tests for the legacy compatibility boundary."""
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

MODULE_PATH = Path(__file__).with_name("legacy_compat.py")
spec = importlib.util.spec_from_file_location("legacy_compat", MODULE_PATH)
compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compat)


class LegacyCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "legacy").mkdir()
        self.member = "DSpecM1D/installation.html"
        self.body = b"<!doctype html><html><head></head><body>old guide</body></html>"
        self.archive = self.root / "legacy" / "snapshot.tar.gz"
        self.record = {
            "legacy_path": "/DSpecM1D/installation.html",
            "new": "/SpecRelab/dspecm1d/installation.html",
            "action": "per-route-migration-to-new-route-after-target-validation",
            "archive_member": self.member,
            "sha256": hashlib.sha256(self.body).hexdigest(),
            "bytes": len(self.body),
        }
        self.mapping = {
            "dspecm1d_routes": [self.record],
            "gplspec_routes": [],
            "quick_start_alias": {"legacy": "https://adcm2.github.io/DSpecM1D/installation.html#quick-start"},
            "verified_target_anchor_sets": {
                "https://adcm2.github.io/SpecRelab/dspecm1d/installation.html": ["quick-start"]
            },
        }
        self.write_archive([(self.member, self.body)])
        self.write_map()

    def tearDown(self):
        self.temp.cleanup()

    def write_archive(self, entries):
        with self.archive.open("wb") as raw:
            with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
                with tarfile.open(fileobj=gz, mode="w", format=tarfile.USTAR_FORMAT) as tf:
                    for name, body in entries:
                        info = tarfile.TarInfo(name)
                        info.size = len(body)
                        tf.addfile(info, io.BytesIO(body))
        self.mapping["legacy_archive"] = {
            "path": "legacy/snapshot.tar.gz",
            "sha256": hashlib.sha256(self.archive.read_bytes()).hexdigest(),
            "files": len(entries),
            "expanded_bytes": sum(len(body) for _, body in entries),
        }
        self.write_map()

    def write_map(self):
        (self.root / "compatibility-map.json").write_text(json.dumps(self.mapping))

    def test_valid_archive_and_hash_are_accepted(self):
        _, bodies = compat.validate_archive(self.root / "compatibility-map.json")
        self.assertEqual(bodies[self.member], self.body)

    def test_bad_body_hash_is_rejected(self):
        self.record["sha256"] = "0" * 64
        self.write_map()
        with self.assertRaisesRegex(compat.CompatibilityError, "archived body mismatch"):
            compat.validate_archive(self.root / "compatibility-map.json")

    def test_path_traversal_in_map_is_rejected(self):
        self.record["archive_member"] = "DSpecM1D/../../outside.html"
        self.write_map()
        with self.assertRaisesRegex(compat.CompatibilityError, "unsafe archive path"):
            compat.validate_archive(self.root / "compatibility-map.json")

    def test_symlink_member_is_rejected(self):
        with self.archive.open("wb") as raw:
            with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
                with tarfile.open(fileobj=gz, mode="w", format=tarfile.USTAR_FORMAT) as tf:
                    info = tarfile.TarInfo(self.member)
                    info.type = tarfile.SYMTYPE
                    info.linkname = "../../outside"
                    tf.addfile(info)
        self.mapping["legacy_archive"].update({
            "sha256": hashlib.sha256(self.archive.read_bytes()).hexdigest(),
            "files": 1,
            "expanded_bytes": 0,
        })
        self.write_map()
        with self.assertRaisesRegex(compat.CompatibilityError, "not a regular file"):
            compat.validate_archive(self.root / "compatibility-map.json")

    def test_extra_archive_member_is_rejected(self):
        self.write_archive([(self.member, self.body), ("DSpecM1D/extra.bin", b"unexpected")])
        self.mapping["legacy_archive"]["files"] = 1
        self.mapping["legacy_archive"]["expanded_bytes"] = len(self.body)
        self.write_map()
        with self.assertRaisesRegex(compat.CompatibilityError, "membership mismatch"):
            compat.validate_archive(self.root / "compatibility-map.json")

    def test_protected_api_body_must_remain_byte_identical(self):
        compat.verify_protected_api_body("DSpecM1D/api/class.html", b"same", b"same")
        with self.assertRaisesRegex(compat.CompatibilityError, "protected DSpecM1D API"):
            compat.verify_protected_api_body("DSpecM1D/api/class.html", b"changed", b"original")
        pdf_record = dict(self.record, archive_member="gplspec/benchfigures/Bench1.pdf", legacy_path="/gplspec/benchfigures/Bench1.pdf", action="retain-static-file")
        self.assertEqual(
            compat.archived_output_body("gplspec/benchfigures/Bench1.pdf", b"\x00pdf\xff", pdf_record, self.mapping),
            b"\x00pdf\xff",
        )

    def test_redirect_keeps_explicit_link_and_fragment_fallback(self):
        body = compat.overlay_html(self.body, self.record, self.mapping["verified_target_anchor_sets"])
        text = body.decode()
        self.assertIn("Open the current SpecRelab documentation", text)
        self.assertIn("next.search=location.search", text)
        self.assertIn("if(!anchors.has(fragment)){return;}", text)
        self.assertIn("new Set([\"quick-start\"])", text)
        self.assertIn("location.replace(next.href)", text)

    def test_gpl_api_guides_migrate_but_dspec_doxygen_stays_protected(self):
        gpl = dict(self.record, archive_member="gplspec/api/api.html", legacy_path="/gplspec/api/api.html", new="/SpecRelab/gplspec/api/api.html")
        dspec_api = dict(self.record, archive_member="DSpecM1D/api/example.html", legacy_path="/DSpecM1D/api/example.html", new="/SpecRelab/dspecm1d/api/example.html")
        self.assertTrue(compat.mapped_manual(gpl))
        self.assertFalse(compat.mapped_manual(dspec_api))

    def test_modeldata_must_match_the_pinned_tree_blobs_and_sizes(self):
        self.mapping["gplspec_routes"] = [{
            "legacy_path": "/gplspec/modeldata/example.nc",
            "source_blob": "a" * 40,
            "bytes": 8,
        }]
        compat.verify_modeldata_tree(self.mapping, {"modeldata/example.nc": ("a" * 40, 8)})
        with self.assertRaisesRegex(compat.CompatibilityError, "differs from pinned Git tree"):
            compat.verify_modeldata_tree(self.mapping, {"modeldata/example.nc": ("b" * 40, 8)})

    def test_unknown_target_metadata_is_rejected(self):
        self.mapping["verified_target_anchor_sets"] = {}
        with self.assertRaisesRegex(compat.CompatibilityError, "lack frozen anchor metadata"):
            compat.validate_target_anchor_metadata(self.mapping)


if __name__ == "__main__":
    unittest.main()
