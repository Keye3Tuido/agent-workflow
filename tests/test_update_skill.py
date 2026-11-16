import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "lucid-workflow" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from update_skill import RELEASE_API, apply_package, fetch_latest_release, inspect_update, main, parse_version


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.installed = self.base / "skills" / "lucid-workflow"
        self.installed.mkdir(parents=True)
        (self.installed / "VERSION").write_text("0.9.0\n", encoding="utf-8")
        (self.installed / "SKILL.md").write_text("old\n", encoding="utf-8")
        self.release = {
            "version": "v1.0.0",
            "asset_url": "https://github.com/Keye3Tuido/lucid-workflow/releases/download/v1.0.0/lucid-workflow.skill",
            "release_url": "https://github.com/Keye3Tuido/lucid-workflow/releases/tag/v1.0.0",
        }

    def tearDown(self):
        self.temporary.cleanup()

    def package(self, version="1.0.0", unsafe=False):
        path = self.base / "lucid-workflow.skill"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("lucid-workflow/SKILL.md", "---\nname: lucid-workflow\ndescription: test\n---\n")
            archive.writestr("lucid-workflow/VERSION", version + "\n")
            archive.writestr("lucid-workflow/scripts/tool.py", "print('ok')\n")
            if unsafe:
                archive.writestr("../escape.txt", "bad\n")
        return path

    def test_version_compare_and_update_detection(self):
        self.assertLess(parse_version("0.9.0"), parse_version("v1.0.0"))
        status = inspect_update(self.installed, self.release)
        self.assertTrue(status["update_available"])
        (self.installed / "VERSION").write_text("1.0.0\n", encoding="utf-8")
        self.assertFalse(inspect_update(self.installed, self.release)["update_available"])

    def test_fetch_latest_release_uses_public_api(self):
        payload = {
            "tag_name": "v1.0.0",
            "html_url": self.release["release_url"],
            "assets": [
                {
                    "name": "lucid-workflow.skill",
                    "browser_download_url": self.release["asset_url"],
                }
            ],
        }
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload)
        with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
            release = fetch_latest_release()
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, RELEASE_API)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 30)
        urlopen.assert_called_once()
        self.assertEqual(release, self.release)

    def test_apply_preserves_backup_and_replaces_version(self):
        result = apply_package(self.package(), self.installed, "v1.0.0")
        self.assertEqual((self.installed / "VERSION").read_text(encoding="utf-8").strip(), "1.0.0")
        self.assertTrue((self.installed / "scripts" / "tool.py").is_file())
        backup = Path(result["backup"])
        self.assertEqual((backup / "VERSION").read_text(encoding="utf-8").strip(), "0.9.0")
        self.assertTrue((backup / "SKILL.md").is_file())

    @unittest.skipIf(os.name == "nt", "POSIX symlink fixture")
    def test_cli_rejects_symlinked_installed_root(self):
        alias = self.base / "skills" / "alias"
        alias.symlink_to(self.installed, target_is_directory=True)
        package = self.package()
        with (
            mock.patch("sys.argv", ["update_skill.py", "apply", "--installed-root", str(alias)]),
            mock.patch("update_skill.fetch_latest_release", return_value=self.release),
            mock.patch("update_skill.download_package", return_value=package),
            mock.patch("builtins.print") as printed,
        ):
            result = main()
        self.assertEqual(result, 2)
        self.assertTrue(alias.is_symlink())
        self.assertEqual((self.installed / "VERSION").read_text(encoding="utf-8").strip(), "0.9.0")
        payload = json.loads(printed.call_args.args[0])
        self.assertFalse(payload["ok"])
        self.assertIn("不得是符号链接", payload["error"])

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
