import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "agent-workflow" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from update_skill import apply_package, inspect_update, parse_version


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.installed = self.base / "skills" / "agent-workflow"
        self.installed.mkdir(parents=True)
        (self.installed / "VERSION").write_text("0.9.0\n", encoding="utf-8")
        (self.installed / "SKILL.md").write_text("old\n", encoding="utf-8")
        self.release = {
            "version": "v1.0.0",
            "asset_url": "https://github.com/Keye3Tuido/agent-workflow/releases/download/v1.0.0/agent-workflow.skill",
            "release_url": "https://github.com/Keye3Tuido/agent-workflow/releases/tag/v1.0.0",
        }

    def tearDown(self):
        self.temporary.cleanup()

    def package(self, version="1.0.0", unsafe=False):
        path = self.base / "agent-workflow.skill"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("agent-workflow/SKILL.md", "---\nname: agent-workflow\ndescription: test\n---\n")
            archive.writestr("agent-workflow/VERSION", version + "\n")
            archive.writestr("agent-workflow/scripts/tool.py", "print('ok')\n")
            if unsafe:
                archive.writestr("../escape.txt", "bad\n")
        return path

    def test_version_compare_and_update_detection(self):
        self.assertLess(parse_version("0.9.0"), parse_version("v1.0.0"))
        status = inspect_update(self.installed, self.release)
        self.assertTrue(status["update_available"])
        (self.installed / "VERSION").write_text("1.0.0\n", encoding="utf-8")
        self.assertFalse(inspect_update(self.installed, self.release)["update_available"])

    def test_apply_preserves_backup_and_replaces_version(self):
        result = apply_package(self.package(), self.installed, "v1.0.0")
        self.assertEqual((self.installed / "VERSION").read_text(encoding="utf-8").strip(), "1.0.0")
        self.assertTrue((self.installed / "scripts" / "tool.py").is_file())
        backup = Path(result["backup"])
        self.assertEqual((backup / "VERSION").read_text(encoding="utf-8").strip(), "0.9.0")
        self.assertTrue((backup / "SKILL.md").is_file())

    def test_unsafe_package_does_not_replace_installation(self):
        before = (self.installed / "VERSION").read_bytes()
        with self.assertRaises(ValueError):
            apply_package(self.package(unsafe=True), self.installed, "v1.0.0")
        self.assertEqual((self.installed / "VERSION").read_bytes(), before)
        self.assertFalse((self.base / "escape.txt").exists())

    def test_version_mismatch_does_not_replace_installation(self):
        with self.assertRaises(ValueError):
            apply_package(self.package(version="9.9.9"), self.installed, "v1.0.0")
        self.assertEqual((self.installed / "VERSION").read_text(encoding="utf-8").strip(), "0.9.0")


if __name__ == "__main__":
    unittest.main()
