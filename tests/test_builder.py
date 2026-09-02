import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build_update.py"
SPEC = importlib.util.spec_from_file_location("build_update", SCRIPT)
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class BuilderTests(unittest.TestCase):
    def test_md5_manifest_round_trip(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "nested").mkdir()
            (root / "a.txt").write_bytes(b"alpha\n")
            (root / "nested/b.bin").write_bytes(b"\x00\x01")
            builder.write_md5_list(root)
            builder.verify_component_md5(root)
            lines = (root / "md5sum.list").read_text().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertTrue(lines[0].endswith("  ./a.txt"))

    def test_archive_has_vendor_style_names(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name) / "root"
            root.mkdir()
            (root / "run.sh").write_text("#!/bin/sh\n")
            output = Path(name) / "component.tar"
            builder.make_tar(root, output, "none", 0)
            import tarfile
            with tarfile.open(output) as archive:
                self.assertIn("./run.sh", archive.getnames())
                self.assertEqual(archive.getmember("./run.sh").mode, 0o755)

    def test_component_selection_requires_software(self):
        with self.assertRaises(SystemExit):
            builder.parse_components("control")
        self.assertEqual(builder.parse_components("software,library"),
                         ["software", "library"])


if __name__ == "__main__":
    unittest.main()

