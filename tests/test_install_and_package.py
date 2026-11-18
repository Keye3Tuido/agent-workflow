import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "lucid-workflow"
SCRIPTS = SKILL / "scripts"
INSTALLER = SCRIPTS / "install_links.py"
sys.path.insert(0, str(SCRIPTS))

from check_doc_forest import validate_config
from session_questions import validate_document as validate_questions_document
from state_files import validate_requirements_data, validate_todos_data
from yaml_common import load_yaml


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.source = self.base / "source" / "lucid-workflow"
        self.source.mkdir(parents=True)
        (self.source / "SKILL.md").write_text(
            "---\nname: lucid-workflow\ndescription: fixture\n---\n", encoding="utf-8"
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
        target = shared / "skills" / "lucid-workflow"
        self.assertTrue((target / "SKILL.md").is_file())
        for host in hosts:
            link = host / "lucid-workflow"
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.resolve(), target.resolve())
        repeated = self.run_installer(shared, hosts, use_existing=True)
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        actions = {item["action"] for item in json.loads(repeated.stdout)["actions"]}
        self.assertIn("existing-reused", actions)
        self.assertIn("already-linked", actions)

    def test_existing_shared_or_host_directory_is_not_overwritten(self):
        shared = self.base / "shared"
        target = shared / "skills" / "lucid-workflow"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text(
            "---\nname: lucid-workflow\ndescription: existing\n---\n", encoding="utf-8"
        )
        existing = self.run_installer(shared, [])
        self.assertEqual(existing.returncode, 1)
        self.assertIn("description: existing", (target / "SKILL.md").read_text(encoding="utf-8"))
        other_shared = self.base / "other-shared"
        host = self.base / "host"
        conflict = host / "lucid-workflow"
        conflict.mkdir(parents=True)
        (conflict / "keep.txt").write_text("keep\n", encoding="utf-8")
        blocked = self.run_installer(other_shared, [host])
        self.assertEqual(blocked.returncode, 1)
        self.assertTrue((conflict / "keep.txt").is_file())
        self.assertFalse((other_shared / "skills" / "lucid-workflow").exists())


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
        self.assertTrue((ROOT / "LICENSE").is_file())
        self.assertTrue((SKILL / "LICENSE").is_file())
        self.assertIn("MIT License", (SKILL / "LICENSE").read_text(encoding="utf-8"))

    def test_assets_follow_their_schemas(self):
        validate_config(load_yaml(SKILL / "assets" / "doc-forest.yml"))
        self.assertEqual(validate_todos_data(load_yaml(SKILL / "assets" / "todos.yml")), [])
        self.assertEqual(
            validate_requirements_data(load_yaml(SKILL / "assets" / "requirements.yml")),
            [],
        )
        self.assertEqual(
            validate_questions_document(SKILL / "assets" / "questions.yml", load_yaml(SKILL / "assets" / "questions.yml")),
            [],
        )
        self.assertTrue((SCRIPTS / "install.py").is_file())
        info = load_yaml(SKILL / "assets" / "info.yml")
        self.assertEqual(info["schema_version"], "1.0")
        self.assertEqual(set(info), {"schema_version", "purpose", "contents_summary"})

    def test_agents_markers_paths_expression_and_gitignore_rule(self):
        text = (SKILL / "assets" / "AGENTS.template.md").read_text(encoding="utf-8")
        self.assertFalse(list(ROOT.rglob("AGENTS.md")), "源仓库不得包含可自动加载的 AGENTS.md")
        for relative in (".todos.yml", ".agent-workflow-audit.jsonl", ".doc-forest.yml", ".doc-forest"):
            self.assertFalse((ROOT / relative).exists(), f"源仓库不得自我应用工作流: {relative}")
        self.assertIn("文件持久化、维护与清理", (SKILL / "references" / "paths.md").read_text(encoding="utf-8"))
        self.assertEqual(text.count("<!-- agent-workflow:start -->"), 1)
        self.assertEqual(text.count("<!-- agent-workflow:end -->"), 1)
        self.assertIn("$HOME/.agents/AGENTS.md", text)
        self.assertIn("%USERPROFILE%\\.agents\\AGENTS.md", text)
        self.assertNotIn("本机", text)
        self.assertNotIn("/Users/user01", text)
        self.assertIn("## 以读者为中心的信息表达", text)
        self.assertIn(".gitignore", text)
        self.assertIn(".agent-workflow-audit.jsonl", text)
        self.assertIn("..todos.yml.lock", text)
        self.assertIn("用户肯定对应成果", text)
        self.assertIn("准许提交", text)
        self.assertIn("待确认队列", text)
        self.assertIn("不得仅凭 Git 已提交", text)
        self.assertIn("最少打扰", text)
        self.assertIn("不重复确认", text)
        self.assertIn("不得从成果肯定推断额外授权", text)
        self.assertIn("不主动", text)
        paths = (SKILL / "references" / "paths.md").read_text(encoding="utf-8")
        self.assertIn("AGENT_WORKFLOW_SESSION_DIR", paths)
        self.assertIn("%LOCALAPPDATA%", paths)
        guidance = (SKILL / "references" / "content-guidance.md").read_text(encoding="utf-8")
        self.assertIn("输入输出", guidance)
        self.assertIn("当前职责与边界", guidance)
        self.assertIn("深化与折叠", guidance)
        self.assertIn("森林深度随维护调整", text)
        self.assertNotIn("通用 14 章", guidance)

    def test_default_mcp_service_files_and_mode_isolation(self):
        for relative in (
            "scripts/coordination/core.py",
            "scripts/coordination/mcp_server.py",
            "scripts/coordination/service_process.py",
            "scripts/coordination/service_control.py",
            "scripts/coordination/install_mcp.py",
            "scripts/coordination/requirements.txt",
            "references/mcp-service.md",
            "references/mcp-validation.md",
        ):
            self.assertTrue((SKILL / relative).is_file(), relative)
        requirements = (SKILL / "scripts/coordination/requirements.txt").read_text(encoding="utf-8")
        self.assertIn("mcp==2.3.0", requirements)
        self.assertIn("PyYAML==6.0.3", requirements)
        guide = (SKILL / "references/mcp-service.md").read_text(encoding="utf-8")
        self.assertIn("禁止在线混写", guide)
        self.assertIn("不得自动改用 CLI", guide)
        self.assertIn("默认 MCP 模式", (SKILL / "assets/AGENTS.template.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
