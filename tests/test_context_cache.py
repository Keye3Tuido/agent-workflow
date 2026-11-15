import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "agent-workflow" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from check_doc_forest import check_forest
from forest_context import find_context
from yaml_common import dump_yaml


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "project"
        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "worker.py").write_text(
            "def worker():\n    return 'needle'\n", encoding="utf-8"
        )
        self.write_yaml(self.root / ".doc-forest.yml", {
            "schema_version": "1.0",
            "write_check": {"excluded_paths": [".doc-forest", ".todos.yml"]},
            "forest": {"allowlist": [], "excluded_paths": [".git"]},
        })
        self.write_info("src", "工作模块", files=[{"name": "worker.py", "purpose": "执行工作"}])
        self.write_info(".", "项目", directories=[{"name": "src"}])

    def tearDown(self):
        self.temporary.cleanup()

    def write_yaml(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_yaml(data), encoding="utf-8")

    def write_info(self, relative, purpose, files=None, directories=None, metadata=None):
        data = {
            "schema_version": "1.0",
            "purpose": purpose,
            "contents_summary": f"{purpose}的内容",
        }
        if files is not None:
            data["files"] = files
        if directories is not None:
            data["directories"] = directories
        if metadata:
            data.update(metadata)
        self.write_yaml(self.root / ".doc-forest" / relative / "info.yml", data)

    def test_cache_hit_avoids_source_reads(self):
        first = find_context(self.root, "工作", keywords=["worker"])
        self.assertEqual(first["cache"]["status"], "built")
        self.assertGreater(first["scan"]["source_reads"], 0)
        second = find_context(self.root, "工作", keywords=["worker"])
        self.assertEqual(second["cache"]["status"], "hit")
        self.assertEqual(second["scan"]["source_reads"], 0)
        self.assertEqual(second["locations"][0]["path"], "src/worker.py")

    def test_source_change_rebuilds_cache(self):
        find_context(self.root, "工作", keywords=["worker"])
        (self.root / "src" / "worker.py").write_text("def worker():\n    return 'new_needle'\n", encoding="utf-8")
        rebuilt = find_context(self.root, "新工作", keywords=["new_needle"])
        self.assertEqual(rebuilt["cache"]["status"], "rebuilt")
        self.assertGreater(rebuilt["scan"]["source_reads"], 0)
        self.assertEqual(rebuilt["locations"][0]["path"], "src/worker.py")

    def test_no_cache_does_not_create_cache_file(self):
        report = find_context(self.root, "工作", keywords=["worker"], use_cache=False)
        self.assertEqual(report["cache"]["status"], "disabled")
        self.assertFalse((self.root / ".cache" / "agent-workflow" / "context.sqlite").exists())

    def test_focus_filters_unrelated_module_and_keeps_core_member(self):
        (self.root / "src" / "nested").mkdir()
        (self.root / "src" / "nested" / "core.py").write_text("CORE = True\n", encoding="utf-8")
        (self.root / "other").mkdir()
        (self.root / "other" / "unrelated.py").write_text("# needle\n", encoding="utf-8")
        self.write_info(
            "src",
            "工作模块",
            files=[{"name": "worker.py", "purpose": "执行工作"}],
            directories=[{"name": "nested"}],
            metadata={"boundary": "只处理当前工作", "entry": "worker"},
        )
        self.write_info(
            "src/nested",
            "核心实现",
            files=[{"name": "core.py", "purpose": "核心契约", "core": True}],
        )
        self.write_info("other", "无关模块")
        self.write_info(".", "项目", directories=[{"name": "other"}, {"name": "src"}])
        report = check_forest(self.root)
        self.assertTrue(report["valid"], report["findings"])
        result = find_context(self.root, "工作", keywords=["worker", "needle"])
        self.assertEqual(result["focus_module"], "src")
        self.assertNotIn("other/unrelated.py", [item["path"] for item in result["locations"]])
        self.assertIn("src/nested/core.py", [item["path"] for item in result["core_members"]])


if __name__ == "__main__":
    unittest.main()
