import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "agent-workflow" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from check_doc_forest import ConfigError, check_forest
from yaml_common import dump_yaml


def directory_entry(name, recurse=True):
    entry = {"name": name}
    if not recurse:
        entry["recurse"] = False
    return entry


def info_data(relative, directories=None, files=None):
    data = {
        "schema_version": "1.0",
        "purpose": f"{relative} 的用途",
        "contents_summary": f"{relative} 的内容概括",
    }
    if files is not None:
        data["files"] = files
    if directories is not None:
        data["directories"] = directories
    return data


class ForestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "project"
        self.root.mkdir()
        self.forest = self.root / ".doc-forest"
        self.write_config()

    def tearDown(self):
        self.temporary.cleanup()

    def write_yaml(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_yaml(data), encoding="utf-8")

    def write_config(self, allowlist=None, excluded=None):
        self.write_yaml(
            self.root / ".doc-forest.yml",
            {
                "schema_version": "1.0",
                "write_check": {"excluded_paths": [".doc-forest", ".todos.yml"]},
                "forest": {
                    "allowlist": allowlist or [],
                    "excluded_paths": excluded
                    if excluded is not None
                    else [".git", "node_modules", ".venv", "venv"],
                },
            },
        )

    def write_info(self, relative, directories=None, files=None):
        target = self.forest if relative == "." else self.forest / relative
        self.write_yaml(target / "info.yml", info_data(relative, directories, files))

    def codes(self, report):
        return {finding["code"] for finding in report["findings"]}

    def test_valid_full_tree(self):
        (self.root / "src" / "module").mkdir(parents=True)
        (self.root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
        (self.root / "README.md").write_text("fixture\n", encoding="utf-8")
        self.write_info(
            ".",
            [directory_entry("src")],
            [{"name": "README.md", "purpose": "测试项目说明"}],
        )
        self.write_info(
            "src",
            [directory_entry("module")],
            [{"name": "main.py", "purpose": "入口"}],
        )
        self.write_info("src/module")
        report = check_forest(self.root)
        self.assertTrue(report["valid"], report["findings"])
        self.assertEqual(report["checked_directories"], [".", "src", "src/module"])

    def test_missing_stale_and_unexpected_files_are_reported(self):
        (self.root / "src").mkdir()
        self.write_info(".", [directory_entry("src")])
        self.write_info("orphan")
        (self.forest / "junk.txt").write_text("junk\n", encoding="utf-8")
        report = check_forest(self.root)
        self.assertFalse(report["valid"])
        self.assertTrue(
            {"missing_directory", "missing_info", "unexpected_directory", "unexpected_file"}
            <= self.codes(report),
            report["findings"],
        )

    def test_dependency_and_configured_build_exclusions(self):
        for relative in (".git", "node_modules/pkg", ".venv/bin", "venv/lib", "frontend/dist", "vendor/lib"):
            (self.root / relative).mkdir(parents=True)
        self.write_config(excluded=[".git", "node_modules", ".venv", "venv", "frontend/dist"])
        self.write_info(".", [directory_entry("frontend"), directory_entry("vendor")])
        self.write_info("frontend")
        self.write_info("vendor", [directory_entry("lib")])
        self.write_info("vendor/lib")
        report = check_forest(self.root)
        self.assertTrue(report["valid"], report["findings"])
        self.assertNotIn("node_modules", report["checked_directories"])
        self.assertNotIn("frontend/dist", report["checked_directories"])
        self.assertIn("vendor", report["checked_directories"])

    def test_allowlist_keeps_only_targets_descendants_and_ancestors(self):
        for relative in ("src/app", "src/other", "docs"):
            (self.root / relative).mkdir(parents=True)
        self.write_config(allowlist=["src/app"])
        self.write_info(".", [directory_entry("src")])
        self.write_info("src", [directory_entry("app")])
        self.write_info("src/app")
        report = check_forest(self.root)
        self.assertTrue(report["valid"], report["findings"])
        self.assertEqual(report["checked_directories"], [".", "src", "src/app"])

    def test_recurse_false_keeps_node_but_prunes_descendants(self):
        (self.root / "generated" / "cache").mkdir(parents=True)
        (self.root / "generated" / "result.txt").write_text("result\n", encoding="utf-8")
        self.write_info(".", [directory_entry("generated", recurse=False)])
        self.write_info(
            "generated",
            files=[{"name": "result.txt", "purpose": "生成结果"}],
        )
        report = check_forest(self.root)
        self.assertTrue(report["valid"], report["findings"])
        self.assertEqual(report["checked_directories"], [".", "generated"])
        self.write_info("generated", [directory_entry("cache")])
        invalid = check_forest(self.root)
        self.assertIn("invalid_info", self.codes(invalid))

    def test_partial_check_covers_path_and_ancestors_only(self):
        (self.root / "src" / "a").mkdir(parents=True)
        (self.root / "src" / "b").mkdir(parents=True)
        self.write_info(".", [directory_entry("src")])
        self.write_info("src", [directory_entry("a"), directory_entry("b")])
        self.write_info("src/a")
        (self.forest / "src" / "b").mkdir(parents=True)
        (self.forest / "src" / "b" / "junk.txt").write_text("junk\n", encoding="utf-8")
        partial = check_forest(self.root, ["src/a"])
        self.assertTrue(partial["valid"], partial["findings"])
        self.assertEqual(partial["checked_directories"], [".", "src", "src/a"])
        full = check_forest(self.root)
        self.assertTrue({"missing_info", "unexpected_file"} <= self.codes(full))

    def test_null_duplicate_and_unknown_yaml_do_not_crash(self):
        self.forest.mkdir()
        (self.forest / "info.yml").write_text("null\n", encoding="utf-8")
        null_report = check_forest(self.root)
        self.assertIn("invalid_info", self.codes(null_report))
        (self.forest / "info.yml").write_text(
            'schema_version: "1.0"\nschema_version: "1.0"\n', encoding="utf-8"
        )
        duplicate_report = check_forest(self.root)
        self.assertIn("invalid_yaml", self.codes(duplicate_report))
        data = info_data(".")
        data["unknown"] = True
        self.write_yaml(self.forest / "info.yml", data)
        unknown_report = check_forest(self.root)
        self.assertIn("invalid_info", self.codes(unknown_report))

    @unittest.skipIf(os.name == "nt", "POSIX symlink fixture")
    def test_directory_symlink_is_not_followed(self):
        external = self.base / "external"
        external.mkdir()
        (self.root / "linked").symlink_to(external, target_is_directory=True)
        self.write_info(".")
        report = check_forest(self.root)
        self.assertTrue(report["valid"], report["findings"])
        self.assertEqual(report["checked_directories"], ["."])

    def test_null_config_section_returns_config_error(self):
        self.write_yaml(self.root / ".doc-forest.yml", {
            "schema_version": "1.0",
            "write_check": None,
            "forest": {"allowlist": [], "excluded_paths": []},
        })
        with self.assertRaises(ConfigError):
            check_forest(self.root)

    def test_noncanonical_path_segments_are_rejected(self):
        from yaml_common import normalize_project_path

        for value in ("src//worker.py", "src/./worker.py", "src/../worker.py", "src/worker.py/"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_project_path(value)
        self.assertEqual(normalize_project_path("src/worker.py"), "src/worker.py")

    def test_json_cli_and_exit_codes(self):
        self.write_info(".")
        completed = subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / "check_doc_forest.py"), "--root", str(self.root), "--format", "json"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(json.loads(completed.stdout)["valid"])
        (self.root / ".doc-forest.yml").unlink()
        missing = subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / "check_doc_forest.py"), "--root", str(self.root)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(missing.returncode, 2)

    def test_module_metadata_and_dependency_targets(self):
        (self.root / "src").mkdir()
        (self.root / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")
        (self.root / "src" / "helper.py").write_text("RULE = True\n", encoding="utf-8")
        self.write_info(
            ".",
            [directory_entry("src")],
            [{
                "name": "main.py",
                "purpose": "编排入口",
                "boundary": "只编排，不持久化",
                "entry": "main",
                "depends_on": [
                    {"target": "src/helper.py", "kind": "call", "reason": "复用规则"}
                ],
            }],
        )
        self.write_info(
            "src",
            files=[{"name": "helper.py", "purpose": "提供规则"}],
        )
        report = check_forest(self.root)
        self.assertTrue(report["valid"], report["findings"])
        self.write_info(
            ".",
            [directory_entry("src")],
            [{
                "name": "main.py",
                "purpose": "编排入口",
                "depends_on": [{"target": "src/missing.py", "reason": "无效目标"}],
            }],
        )
        invalid = check_forest(self.root)
        self.assertIn("invalid_info", self.codes(invalid))

    def test_partial_path_must_stay_inside_project(self):
        self.write_info(".")
        with self.assertRaises(ConfigError):
            check_forest(self.root, ["../outside"])


if __name__ == "__main__":
    unittest.main()
