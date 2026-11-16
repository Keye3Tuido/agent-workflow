import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "lucid-workflow" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from check_doc_forest import check_forest
from forest_context import find_context
from yaml_common import dump_yaml


class EdgeCaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "project"
        (self.root / "src").mkdir(parents=True)
        self.write_yaml(self.root / ".doc-forest.yml", {
            "schema_version": "1.0",
            "write_check": {"excluded_paths": [".doc-forest", ".todos.yml"]},
            "forest": {"allowlist": [], "excluded_paths": [".git"]},
        })
        self.write_info(".", "项目", directories=[{"name": "src"}])
        self.write_info("src", "源码模块")

    def tearDown(self):
        self.temporary.cleanup()

    def write_yaml(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_yaml(data), encoding="utf-8")

    def write_info(self, relative, purpose, files=None, directories=None, metadata=None):
        data = {"schema_version": "1.0", "purpose": purpose, "contents_summary": f"{purpose}的内容"}
        if files is not None:
            data["files"] = files
        if directories is not None:
            data["directories"] = directories
        if metadata:
            data.update(metadata)
        self.write_yaml(self.root / ".doc-forest" / relative / "info.yml", data)

    def add_source(self, name, content):
        path = self.root / "src" / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def test_special_sql_characters_are_literal(self):
        self.add_source("special.py", "literal \"% value\n")
        report = find_context(self.root, "特殊字符", keywords=['"%'])
        self.assertEqual(report["locations"][0]["path"], "src/special.py")

    def test_short_unicode_and_ascii_casefold(self):
        self.add_source("restore.py", "恢复购买\nRestorePurchases\n")
        report = find_context(self.root, "恢复", keywords=["恢复", "restorepurchases"])
        matched = {keyword for item in report["locations"] for keyword in item["matches"]}
        self.assertEqual(matched, {"恢复", "restorepurchases"})

    def test_non_ascii_casefold_matches_with_and_without_cache(self):
        self.add_source("restore.py", "Änderung\n")
        cached = find_context(self.root, "Unicode", keywords=["änderung"])
        direct = find_context(self.root, "Unicode", keywords=["änderung"], use_cache=False)
        expected = ["src/restore.py"]
        self.assertEqual([item["path"] for item in cached["locations"]], expected)
        self.assertEqual([item["path"] for item in direct["locations"]], expected)

    def test_deleted_directory_rebuilds_cache(self):
        import shutil

        self.add_source("worker.py", "worker\n")
        find_context(self.root, "工作", keywords=["worker"])
        shutil.rmtree(self.root / "src")
        report = find_context(self.root, "工作", keywords=["worker"])
        self.assertEqual(report["cache"]["status"], "rebuilt")
        self.assertEqual(report["locations"], [])

    def test_corrupt_cache_rebuilds(self):
        self.add_source("worker.py", "worker\n")
        find_context(self.root, "工作", keywords=["worker"])
        cache = self.root / ".cache" / "agent-workflow" / "context.sqlite"
        cache.write_text("not a database", encoding="utf-8")
        report = find_context(self.root, "工作", keywords=["worker"])
        self.assertEqual(report["cache"]["status"], "rebuilt")
        self.assertEqual(report["locations"][0]["path"], "src/worker.py")

    def test_added_file_rebuilds_cache(self):
        self.add_source("worker.py", "alpha\n")
        find_context(self.root, "旧内容", keywords=["alpha"])
        self.add_source("new_file.py", "omega\n")
        report = find_context(self.root, "新内容", keywords=["omega"])
        self.assertEqual(report["cache"]["status"], "rebuilt")
        self.assertEqual(report["locations"][0]["path"], "src/new_file.py")

    def test_deleted_file_rebuilds_cache(self):
        path = self.add_source("worker.py", "worker\n")
        find_context(self.root, "工作", keywords=["worker"])
        path.unlink()
        report = find_context(self.root, "工作", keywords=["worker"])
        self.assertEqual(report["cache"]["status"], "rebuilt")
        self.assertEqual(report["locations"], [])

    def test_info_change_rebuilds_cache(self):
        self.add_source("worker.py", "worker\n")
        self.write_info("src", "源码模块", files=[{"name": "worker.py", "purpose": "旧职责"}])
        find_context(self.root, "工作", keywords=["worker"])
        self.write_info("src", "源码模块", files=[{"name": "worker.py", "purpose": "新的唯一职责"}])
        report = find_context(self.root, "新职责", keywords=["新的唯一职责"])
        self.assertEqual(report["cache"]["status"], "rebuilt")
        self.assertEqual(report["locations"][0]["path"], "src/worker.py")

    def test_binary_content_is_skipped(self):
        self.add_source("blob.bin", b"\0secret\n")
        report = find_context(self.root, "二进制", keywords=["secret"])
        self.assertEqual(report["locations"], [])
        self.assertGreater(report["scan"]["skipped_binary_files"], 0)

    @unittest.skipIf(os.name == "nt", "POSIX symlink fixture")
    def test_symlink_is_not_followed(self):
        external = Path(self.temporary.name) / "external.py"
        external.write_text("secret\n", encoding="utf-8")
        (self.root / "src" / "link.py").symlink_to(external)
        report = find_context(self.root, "符号链接", keywords=["secret"])
        self.assertEqual(report["locations"], [])

    def test_concurrent_cache_build_and_hit(self):
        self.add_source("worker.py", "worker\n")

        def query(_):
            return find_context(self.root, "工作", keywords=["worker"])

        with ThreadPoolExecutor(max_workers=4) as executor:
            reports = list(executor.map(query, range(4)))
        self.assertTrue(all(report["locations"][0]["path"] == "src/worker.py" for report in reports))
        self.assertTrue(all(report["cache"]["status"] in ("built", "hit") for report in reports))

    def test_no_focus_preserves_ambiguous_modules(self):
        for name in ("alpha", "beta"):
            (self.root / name).mkdir()
            (self.root / name / "shared.py").write_text("shared\n", encoding="utf-8")
            self.write_info(name, name, metadata={"boundary": f"{name} 边界"})
        self.write_info(".", "项目", directories=[{"name": "alpha"}, {"name": "beta"}, {"name": "src"}])
        report = find_context(self.root, "共享", keywords=["shared"], focus=False)
        self.assertIsNone(report["focus_module"])
        self.assertEqual(len(report["locations"]), 2)
        self.assertEqual(report["scan"]["focus_filtered_out"], 0)

    def test_duplicate_dependency_and_core_false_are_rejected(self):
        self.add_source("worker.py", "worker\n")
        entry = {"name": "worker.py", "purpose": "工作", "core": False}
        self.write_info("src", "源码模块", files=[entry])
        self.assertIn("invalid_info", {item["code"] for item in check_forest(self.root)["findings"]})
        entry.pop("core")
        entry["depends_on"] = [
            {"target": "worker.py", "reason": "重复关系"},
            {"target": "worker.py", "reason": "重复关系"},
        ]
        self.write_info("src", "源码模块", files=[entry])
        self.assertIn("invalid_info", {item["code"] for item in check_forest(self.root)["findings"]})

    def test_invalid_dependency_kind_is_rejected(self):
        self.add_source("worker.py", "worker\n")
        for kind in ("unknown", ["call"]):
            with self.subTest(kind=kind):
                self.write_info("src", "源码模块", files=[{
                    "name": "worker.py",
                    "purpose": "工作",
                    "depends_on": [{"target": "worker.py", "kind": kind, "reason": "无效类型"}],
                }])
                self.assertIn("invalid_info", {item["code"] for item in check_forest(self.root)["findings"]})

    def test_more_than_twenty_core_members_are_not_truncated(self):
        self.add_source("worker.py", "needle\n")
        files = [{"name": "worker.py", "purpose": "工作"}]
        for index in range(25):
            name = f"core_{index:02d}.py"
            self.add_source(name, "pass\n")
            files.append({"name": name, "purpose": "核心契约", "core": True})
        self.write_info("src", "源码模块", files=files, metadata={"boundary": "核心模块"})
        report = find_context(self.root, "核心", keywords=["needle"])
        self.assertEqual(len(report["core_members"]), 25)


if __name__ == "__main__":
    unittest.main()
