import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "lucid-workflow" / "scripts"
SCRIPT = SCRIPTS / "install.py"
sys.path.insert(0, str(SCRIPTS))

from install import END, START, merge_guidance


class OneCommandInstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def run_installer(self, *arguments):
        environment = os.environ.copy()
        environment["HOME"] = str(self.home)
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), *map(str, arguments)],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )

    def test_merge_guidance_preserves_other_rules_and_replaces_block(self):
        template = ROOT / "lucid-workflow" / "assets" / "AGENTS.template.md"
        target = self.home / "AGENTS.md"
        target.write_text(f"保留前缀\n{START}\n旧规则\n{END}\n保留后缀\n", encoding="utf-8")
        merge_guidance(template, target)
        text = target.read_text(encoding="utf-8")
        self.assertIn("保留前缀", text)
        self.assertIn("保留后缀", text)
        self.assertNotIn("旧规则", text)
        self.assertEqual(text.count(START), 1)
        self.assertEqual(text.count(END), 1)
        self.assertTrue(list(self.home.glob("AGENTS.md.backup-lucid-*")))

    def test_one_command_install_creates_shared_link_and_guidance(self):
        completed = self.run_installer("--host", "claude-code")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        shared = self.home / ".agents" / "skills" / "lucid-workflow"
        self.assertTrue((shared / "SKILL.md").is_file())
        link = self.home / ".claude" / "skills" / "lucid-workflow"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), shared.resolve())
        for guidance in (self.home / ".agents" / "AGENTS.md", self.home / ".claude" / "CLAUDE.md"):
            text = guidance.read_text(encoding="utf-8")
            self.assertEqual(text.count(START), 1)
            self.assertIn("Lucid Workflow", text)
        repeated = self.run_installer("--host", "claude-code", "--use-existing")
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertEqual((self.home / ".claude" / "CLAUDE.md").read_text(encoding="utf-8").count(START), 1)
        actions = [item["action"] for item in json.loads(repeated.stdout)["actions"]]
        self.assertEqual(actions.count("guidance-unchanged"), 2)
        self.assertFalse(list(self.home.rglob("*.backup-lucid-*")))

    def test_kimi_hosts_explicitly_use_shared_installation(self):
        completed = self.run_installer("--host", "kimi", "--host", "kimi-code")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["hosts"], [])
        self.assertEqual(result["shared_only_hosts"], ["kimi", "kimi-code"])
        shared_actions = [item for item in result["actions"] if item["action"] == "shared-only"]
        self.assertEqual([item["host"] for item in shared_actions], ["kimi", "kimi-code"])
        self.assertTrue((self.home / ".agents" / "skills" / "lucid-workflow" / "SKILL.md").is_file())
        self.assertFalse((self.home / ".kimi").exists())
        self.assertFalse((self.home / ".kimi-code").exists())

    def test_auto_only_uses_existing_host_directories(self):
        (self.home / ".codex").mkdir()
        completed = self.run_installer()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["hosts"], ["codex"])
        self.assertTrue((self.home / ".codex" / "skills" / "lucid-workflow").is_symlink())
        self.assertFalse((self.home / ".claude").exists())


if __name__ == "__main__":
    unittest.main()
