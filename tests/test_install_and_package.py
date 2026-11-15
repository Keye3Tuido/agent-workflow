import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "agent-workflow"
SCRIPTS = SKILL / "scripts"
INSTALLER = SCRIPTS / "install_links.py"
sys.path.insert(0, str(SCRIPTS))

from check_doc_forest import validate_config
from state_files import validate_requirements_data, validate_todos_data
from yaml_common import load_yaml


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.source = self.base / "source" / "agent-workflow"
        self.source.mkdir(parents=True)
        (self.source / "SKILL.md").write_text(
            "---\nname: agent-workflow\ndescription: fixture\n---\n", encoding="utf-8"
        )
        (self.source / "scripts").mkdir()
        (self.source / "scripts" / "tool.py").write_text("print('ok')\n", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def run_installer(self, shared, hosts, use_existing=False):
        command = [
            sys.executable,
            "-B",
            str(INSTALLER),
            "--source",
            str(self.source),
            "--shared-root",
            str(shared),
        ]
        for host in hosts:
            command.extend(["--host-skill-dir", str(host)])
        if use_existing:
            command.append("--use-existing")
        return subprocess.run(command, capture_output=True, text=True, check=False)

    def test_single_authoritative_entity_and_host_links(self):
        shared = self.base / "shared"
        hosts = [self.base / "claude" / "skills", self.base / "codex" / "skills"]
        installed = self.run_installer(shared, hosts)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        target = shared / "skills" / "agent-workflow"
        self.assertTrue((target / "SKILL.md").is_file())
        for host in hosts:
            link = host / "agent-workflow"
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.resolve(), target.resolve())
        repeated = self.run_installer(shared, hosts, use_existing=True)
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        actions = {item["action"] for item in json.loads(repeated.stdout)["actions"]}
        self.assertIn("existing-reused", actions)
        self.assertIn("already-linked", actions)

    def test_existing_shared_or_host_directory_is_not_overwritten(self):
        shared = self.base / "shared"
        target = shared / "skills" / "agent-workflow"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text(
            "---\nname: agent-workflow\ndescription: existing\n---\n", encoding="utf-8"
        )
        existing = self.run_installer(shared, [])
        self.assertEqual(existing.returncode, 1)
        self.assertIn("description: existing", (target / "SKILL.md").read_text(encoding="utf-8"))
        other_shared = self.base / "other-shared"
        host = self.base / "host"
        conflict = host / "agent-workflow"
        conflict.mkdir(parents=True)
        (conflict / "keep.txt").write_text("keep\n", encoding="utf-8")
        blocked = self.run_installer(other_shared, [host])
        self.assertEqual(blocked.returncode, 1)
        self.assertTrue((conflict / "keep.txt").is_file())
        self.assertFalse((other_shared / "skills" / "agent-workflow").exists())


class PackageTests(unittest.TestCase):
    def test_skill_frontmatter_and_local_references(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        frontmatter = yaml.safe_load(text.split("---", 2)[1])
        self.assertEqual(set(frontmatter), {"name", "description"})
        self.assertEqual(frontmatter["name"], SKILL.name)
        for reference in ("yaml-schemas.md", "paths.md", "content-guidance.md"):
            self.assertIn(f"references/{reference}", text)
            self.assertTrue((SKILL / "references" / reference).is_file())
        for name in ("README.md", "INSTALLATION_GUIDE.md", "CHANGELOG.md"):
            self.assertFalse((SKILL / name).exists())

    def test_assets_follow_their_schemas(self):
        validate_config(load_yaml(SKILL / "assets" / "doc-forest.yml"))
        self.assertEqual(validate_todos_data(load_yaml(SKILL / "assets" / "todos.yml")), [])
        self.assertEqual(
            validate_requirements_data(load_yaml(SKILL / "assets" / "requirements.yml")),
            [],
        )
        info = load_yaml(SKILL / "assets" / "info.yml")
        self.assertEqual(info["schema_version"], "1.0")
        self.assertEqual(set(info), {"schema_version", "purpose", "contents_summary"})

    def test_agents_markers_paths_expression_and_gitignore_rule(self):
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertEqual(text.count("<!-- agent-workflow:start -->"), 1)
        self.assertEqual(text.count("<!-- agent-workflow:end -->"), 1)
        self.assertIn("$HOME/.agents/AGENTS.md", text)
        self.assertIn("%USERPROFILE%\\.agents\\AGENTS.md", text)
        self.assertNotIn("本机", text)
        self.assertNotIn("/Users/user01", text)
        self.assertIn("## 以读者为中心的信息表达", text)
        self.assertIn(".gitignore", text)
        self.assertIn("不主动", text)
        paths = (SKILL / "references" / "paths.md").read_text(encoding="utf-8")
        self.assertIn("AGENT_WORKFLOW_SESSION_DIR", paths)
        self.assertIn("%LOCALAPPDATA%", paths)
        guidance = (SKILL / "references" / "content-guidance.md").read_text(encoding="utf-8")
        self.assertIn("输入输出", guidance)
        self.assertIn("当前职责与边界", guidance)
        self.assertNotIn("通用 14 章", guidance)


if __name__ == "__main__":
    unittest.main()
