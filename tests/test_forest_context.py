import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "lucid-workflow" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from forest_context import find_context
from yaml_common import dump_yaml


def entry(name, recurse=True):
    value = {"name": name}
    if not recurse:
        value["recurse"] = False
    return value


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "project"
        self.root.mkdir()
        self.write_yaml(
            self.root / ".doc-forest.yml",
            {
                "schema_version": "1.0",
                "write_check": {"excluded_paths": [".doc-forest", ".todos.yml"]},
                "forest": {
                    "allowlist": [],
                    "excluded_paths": [".git", "node_modules", ".venv", "venv"],
                },
            },
        )

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
        target = self.root / ".doc-forest" / relative / "info.yml"
        self.write_yaml(target, data)

    def build_project(self):
        (self.root / "src").mkdir()
        (self.root / "tests").mkdir()
        (self.root / "src" / "checker.py").write_text(
            "# 森林检查\ndef check_forest():\n    return 'structure'\n", encoding="utf-8"
        )
        (self.root / "src" / "unrelated.py").write_text("print('other')\n", encoding="utf-8")
        (self.root / "tests" / "test_checker.py").write_text(
            "def test_checker():\n    assert check_forest()\n", encoding="utf-8"
        )
        (self.root / "node_modules" / "pkg").mkdir(parents=True)
        (self.root / "node_modules" / "pkg" / "skip.py").write_text("# 森林检查\n", encoding="utf-8")
        self.write_info(".", "项目", directories=[entry("src"), entry("tests")])
        self.write_info(
            "src",
            "森林检查工具",
            files=[{"name": "checker.py", "purpose": "执行结构检查"}],
        )
        self.write_info(
            "tests",
            "测试",
            files=[{"name": "test_checker.py", "purpose": "检查器回归测试"}],
        )

    def test_summary_path_and_content_produce_ranked_locations_and_lines(self):
        self.build_project()
        report = find_context(self.root, "森林检查", keywords=["森林检查"], max_results=2)
        paths = [item["path"] for item in report["locations"]]
        self.assertIn("src/checker.py", paths)
        self.assertNotIn("src/unrelated.py", paths)
        self.assertNotIn("node_modules/pkg/skip.py", paths)
        checker = next(item for item in report["locations"] if item["path"] == "src/checker.py")
        self.assertEqual(checker["lines"][0]["line"], 1)
        self.assertIn("content", checker["reasons"])
        self.assertIn(".doc-forest/src/info.yml", report["navigation"])

    def test_related_test_is_added_even_without_keyword_match(self):
        self.build_project()
        (self.root / "src" / "worker.py").write_text("def run():\n    return 'needle'\n", encoding="utf-8")
        (self.root / "tests" / "test_worker.py").write_text("assert run()\n", encoding="utf-8")
        self.write_info(
            "src",
            "森林检查工具",
            files=[{"name": "worker.py", "purpose": "执行工作"}],
        )
        report = find_context(
            self.root,
            "定位工作单元",
            keywords=["needle"],
            anchors=["src/worker.py"],
        )
        self.assertTrue(any(item["path"] == "tests/test_worker.py" for item in report["related_tests"]))

    def test_recurse_false_prunes_content_scan(self):
        (self.root / "generated" / "cache").mkdir(parents=True)
        (self.root / "generated" / "result.txt").write_text("needle\n", encoding="utf-8")
        (self.root / "generated" / "cache" / "hidden.txt").write_text("needle\n", encoding="utf-8")
        self.write_info(".", "项目", directories=[entry("generated", recurse=False)])
        self.write_info("generated", "生成结果")
        report = find_context(self.root, "needle", keywords=["needle"])
        paths = [item["path"] for item in report["locations"]]
        self.assertEqual(paths, ["generated/result.txt"])
        self.assertEqual(report["scan"]["directories"], 2)

    def test_result_limit_and_large_file_skipping_are_reported(self):
        self.build_project()
        (self.root / "src" / "forest.py").write_text("# 森林检查\n", encoding="utf-8")
        limited = find_context(self.root, "森林检查", keywords=["森林检查"], max_results=1)
        self.assertEqual(limited["scan"]["omitted_candidates"], 1)
        large = find_context(
            self.root,
            "森林检查",
            keywords=["森林检查"],
            max_file_bytes=5,
        )
        self.assertGreater(large["scan"]["skipped_large_files"], 0)
        self.assertTrue(all(not item["lines"] for item in large["locations"]))

    def test_explicit_keyword_is_not_diluted_by_task_text(self):
        self.build_project()
        (self.root / "src" / "state.py").write_text("def validate_todos_data():\n    pass\n", encoding="utf-8")
        (self.root / "guide.md").write_text("YAML YAML YAML\n", encoding="utf-8")
        report = find_context(
            self.root,
            "YAML 校验",
            keywords=["validate_todos_data"],
        )
        paths = [item["path"] for item in report["locations"]]
        self.assertIn("src/state.py", paths)
        self.assertNotIn("guide.md", paths)
        self.assertEqual(report["keywords"], ["validate_todos_data"])

    def test_module_fields_are_searchable_and_expand_related_modules(self):
        self.build_project()
        (self.root / "src" / "helper.py").write_text("RULE = True\n", encoding="utf-8")
        self.write_info(
            "src",
            "森林检查工具",
            files=[{
                "name": "checker.py",
                "purpose": "执行结构检查",
                "boundary": "只读校验",
                "entry": "check_forest",
                "depends_on": [{"target": "helper.py", "reason": "共享检查规则"}],
            }],
        )
        report = find_context(self.root, "共享检查规则", keywords=["共享检查规则"])
        paths = [item["path"] for item in report["locations"]]
        self.assertIn("src/checker.py", paths)
        self.assertEqual(report["related_modules"][0]["target"], "src/helper.py")
        entry_report = find_context(self.root, "入口", keywords=["check_forest"])
        self.assertIn("src/checker.py", [item["path"] for item in entry_report["locations"]])

    def test_nested_file_inherits_module_dependencies(self):
        self.build_project()
        (self.root / "src" / "helper.py").write_text("RULE = True\n", encoding="utf-8")
        (self.root / "src" / "nested").mkdir()
        (self.root / "src" / "nested" / "worker.py").write_text("def run():\n    pass\n", encoding="utf-8")
        self.write_info(
            "src",
            "森林检查工具",
            files=[{
                "name": "checker.py",
                "purpose": "执行结构检查",
            }],
            directories=[entry("nested")],
            metadata={"depends_on": [{"target": "helper.py", "reason": "共享检查规则"}]},
        )
        self.write_info("src/nested", "嵌套实现")
        report = find_context(
            self.root,
            "嵌套任务",
            keywords=["worker"],
            anchors=["src/nested/worker.py"],
        )
        self.assertEqual(report["related_modules"][0]["target"], "src/helper.py")

    def test_keyword_test_in_same_module_is_added(self):
        self.build_project()
        (self.root / "src" / "worker.py").write_text("def run():\n    return 'needle'\n", encoding="utf-8")
        (self.root / "tests" / "RecoveryPolicyTest.cpp").write_text("TEST(needle) {}\n", encoding="utf-8")
        self.write_info(
            "src",
            "森林检查工具",
            files=[{"name": "worker.py", "purpose": "执行工作"}],
        )
        report = find_context(
            self.root,
            "恢复策略",
            keywords=["needle"],
            anchors=["src/worker.py"],
        )
        paths = [item["path"] for item in report["locations"] + report["related_tests"]]
        self.assertIn("tests/RecoveryPolicyTest.cpp", paths)

    def test_no_match_is_empty_not_fabricated(self):
        self.build_project()
        report = find_context(self.root, "不存在的标识", keywords=["missing_identifier"])
        self.assertEqual(report["locations"], [])
        self.assertEqual(report["related_tests"], [])


if __name__ == "__main__":
    unittest.main()
