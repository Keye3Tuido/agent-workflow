import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "agent-workflow" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from check_doc_forest import ConfigError
from forest_architecture import build_architecture
from yaml_common import dump_yaml


class ArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "project"
        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "worker.py").write_text("def main():\n    pass\n", encoding="utf-8")
        (self.root / "src" / "helper.py").write_text("RULE = True\n", encoding="utf-8")
        self.write_yaml(self.root / ".doc-forest.yml", {
            "schema_version": "1.0",
            "write_check": {"excluded_paths": [".doc-forest", ".todos.yml"]},
            "forest": {"allowlist": [], "excluded_paths": [".git"]},
        })
        self.write_yaml(self.root / ".doc-forest" / "info.yml", {
            "schema_version": "1.0",
            "purpose": "项目",
            "contents_summary": "模块夹具",
            "directories": [{"name": "src"}],
        })
        self.write_yaml(self.root / ".doc-forest" / "src" / "info.yml", {
            "schema_version": "1.0",
            "purpose": "业务模块",
            "contents_summary": "编排和规则",
            "boundary": "仅处理业务，不维护基础设施",
            "entry": "worker.main",
            "files": [
                {
                    "name": "helper.py",
                    "purpose": "提供规则",
                },
                {
                    "name": "worker.py",
                    "purpose": "编排任务",
                    "boundary": "只编排，不持久化",
                    "entry": "main",
                    "depends_on": [{"target": "helper.py", "reason": "复用规则"}],
                },
            ],
        })

    def tearDown(self):
        self.temporary.cleanup()

    def write_yaml(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_yaml(data), encoding="utf-8")

    def test_file_module_shows_direct_dependency(self):
        report = build_architecture(self.root, "src/worker.py")
        self.assertEqual(report["module"]["boundary"], "只编排，不持久化")
        self.assertEqual(report["module"]["entry"], "main")
        self.assertEqual(report["depends_on"][0]["target"], "src/helper.py")
        self.assertEqual(report["dependents"], [])

    def test_reverse_dependency_is_derived_not_stored(self):
        report = build_architecture(self.root, "src/helper.py")
        self.assertEqual(report["depends_on"], [])
        self.assertEqual(report["dependents"][0]["source"], "src/worker.py")

    def test_directory_module_shows_members_and_entry(self):
        report = build_architecture(self.root, "src")
        self.assertEqual(report["module"]["entry"], "worker.main")
        self.assertEqual(len(report["module"]["members"]), 2)
        with self.assertRaises(ConfigError):
            build_architecture(self.root, "src/missing.py")


if __name__ == "__main__":
    unittest.main()
