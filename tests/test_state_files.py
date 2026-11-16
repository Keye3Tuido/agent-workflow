import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "lucid-workflow" / "scripts"
SCRIPT = SCRIPTS / "state_files.py"
sys.path.insert(0, str(SCRIPTS))

from yaml_common import load_yaml


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.project = self.base / "project"
        self.session = self.base / "session"
        self.project.mkdir()
        self.session.mkdir()
        self.requirements = self.session / "requirements.yml"
        self.todos = self.project / ".todos.yml"

    def tearDown(self):
        self.temporary.cleanup()

    def run_state(self, *arguments):
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), *map(str, arguments)],
            capture_output=True,
            text=True,
            check=False,
        )

    def session_arguments(self):
        return (
            "--file",
            self.requirements,
            "--session-id",
            "session-test",
            "--project-root",
            self.project,
        )

    def init_requirements(self):
        result = self.run_state("requirements", "init", *self.session_arguments())
        self.assertEqual(result.returncode, 0, result.stderr)

    def init_todos(self):
        result = self.run_state("todos", "init", "--file", self.todos)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_requirements_lifecycle_binding_and_project_exclusion(self):
        inside = self.run_state(
            "requirements",
            "init",
            "--file",
            self.project / "requirements.yml",
            "--session-id",
            "session-test",
            "--project-root",
            self.project,
        )
        self.assertEqual(inside.returncode, 1)
        self.assertFalse((self.project / "requirements.yml").exists())
        self.init_requirements()
        added = self.run_state(
            "requirements",
            "upsert",
            *self.session_arguments(),
            "--text",
            "保留明确要求",
            "--polarity",
            "required",
            "--scope",
            "session",
            "--source-quote",
            "用户要求原文",
        )
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertEqual(json.loads(added.stdout)["id"], "REQ-001")
        temporary = self.run_state(
            "requirements",
            "upsert",
            *self.session_arguments(),
            "--text",
            "禁止泄露凭证",
            "--polarity",
            "forbidden",
            "--scope",
            "phase",
            "--source-quote",
            "本阶段禁止泄露凭证",
            "--expires-when",
            "本阶段结束",
        )
        self.assertEqual(temporary.returncode, 0, temporary.stderr)
        results = [
            {"requirement_id": "REQ-001", "status": "met", "evidence": "已记录", "reason": None},
            {"requirement_id": "REQ-002", "status": "unmet", "evidence": "尚未执行", "reason": "等待操作授权"},
        ]
        reviewed = self.run_state(
            "requirements",
            "review",
            *self.session_arguments(),
            "--results",
            json.dumps(results, ensure_ascii=False),
        )
        self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
        data = load_yaml(self.requirements)
        self.assertEqual(len(data["response_review"]["results"]), 2)
        before = self.requirements.read_bytes()
        wrong_session = self.run_state(
            "requirements",
            "remove",
            "--file",
            self.requirements,
            "--session-id",
            "other-session",
            "--project-root",
            self.project,
            "--id",
            "REQ-001",
        )
        self.assertEqual(wrong_session.returncode, 1)
        self.assertEqual(self.requirements.read_bytes(), before)
        removed = self.run_state("requirements", "remove", *self.session_arguments(), "--id", "REQ-001")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        data = load_yaml(self.requirements)
        self.assertEqual(data["next_requirement_number"], 3)
        self.assertEqual(data["response_review"]["results"], [])
        updated = self.run_state(
            "requirements",
            "upsert",
            *self.session_arguments(),
            "--id",
            "REQ-002",
            "--text",
            "更新后的临时要求",
        )
        self.assertEqual(updated.returncode, 0, updated.stderr)
        self.assertEqual(load_yaml(self.requirements)["requirements"][0]["text"], "更新后的临时要求")
        validated = self.run_state("requirements", "validate", "--file", self.requirements)
        self.assertEqual(validated.returncode, 0, validated.stderr)

    def test_defaults_and_empty_fields_are_omitted(self):
        self.init_requirements()
        added = self.run_state(
            "requirements",
            "upsert",
            *self.session_arguments(),
            "--text",
            "只保留必要字段",
        )
        self.assertEqual(added.returncode, 0, added.stderr)
        requirement = load_yaml(self.requirements)["requirements"][0]
        self.assertEqual(set(requirement), {"id", "text"})
        self.init_todos()
        added_todo = self.run_state(
            "todos",
            "add",
            "--file",
            self.todos,
            "--title",
            "精简任务",
            "--acceptance-criteria",
            "无空字段",
        )
        self.assertEqual(added_todo.returncode, 0, added_todo.stderr)
        task = load_yaml(self.todos)["tasks"][0]
        self.assertEqual(
            set(task),
            {"id", "title", "acceptance_criteria", "status", "created_at", "updated_at"},
        )
        self.assertNotIn("dependencies", task)
        self.assertNotIn("verification", task)

    def test_todos_transitions_evidence_cleanup_and_atomic_failure(self):
        self.init_todos()
        first = self.run_state(
            "todos",
            "add",
            "--file",
            self.todos,
            "--title",
            "第一项",
            "--acceptance-criteria",
            "验证通过",
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.run_state(
            "todos",
            "add",
            "--file",
            self.todos,
            "--title",
            "第二项",
            "--acceptance-criteria",
            "检查通过",
            "--dependency",
            "TASK-001",
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        working = self.run_state(
            "todos",
            "transition",
            "--file",
            self.todos,
            "--id",
            "TASK-001",
            "--to",
            "working",
            "--executor",
            "agent-a",
        )
        self.assertEqual(working.returncode, 0, working.stderr)
        conflict = self.run_state(
            "todos",
            "transition",
            "--file",
            self.todos,
            "--id",
            "TASK-002",
            "--to",
            "working",
            "--executor",
            "agent-a",
        )
        self.assertEqual(conflict.returncode, 1)
        before = self.todos.read_bytes()
        missing_evidence = self.run_state(
            "todos",
            "transition",
            "--file",
            self.todos,
            "--id",
            "TASK-001",
            "--to",
            "done",
        )
        self.assertEqual(missing_evidence.returncode, 1)
        self.assertEqual(self.todos.read_bytes(), before)
        done = self.run_state(
            "todos",
            "transition",
            "--file",
            self.todos,
            "--id",
            "TASK-001",
            "--to",
            "done",
            "--verification",
            "自动化测试通过",
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        accepted = self.run_state(
            "todos",
            "transition",
            "--file",
            self.todos,
            "--id",
            "TASK-001",
            "--to",
            "accepted",
            "--acceptance-evidence",
            "用户明确回复验收通过",
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        cleaned = self.run_state("todos", "clean-accepted", "--file", self.todos)
        self.assertEqual(cleaned.returncode, 0, cleaned.stderr)
        data = load_yaml(self.todos)
        self.assertEqual([task["id"] for task in data["tasks"]], ["TASK-002"])
        self.assertEqual(data["tasks"][0].get("dependencies", []), [])
        blocked = self.run_state(
            "todos",
            "set-blocked",
            "--file",
            self.todos,
            "--id",
            "TASK-002",
            "--reason",
            "等待用户事实",
        )
        self.assertEqual(blocked.returncode, 0, blocked.stderr)
        cleared = self.run_state("todos", "clear-blocked", "--file", self.todos, "--id", "TASK-002")
        self.assertEqual(cleared.returncode, 0, cleared.stderr)
        validated = self.run_state("todos", "validate", "--file", self.todos)
        self.assertEqual(validated.returncode, 0, validated.stderr)

    def test_non_string_fields_return_validation_errors_without_traceback(self):
        from yaml_common import dump_yaml

        self.init_todos()
        added = self.run_state(
            "todos", "add", "--file", self.todos,
            "--title", "校验任务", "--acceptance-criteria", "无异常",
        )
        self.assertEqual(added.returncode, 0, added.stderr)
        data = load_yaml(self.todos)
        data["tasks"][0]["status"] = []
        self.todos.write_text(dump_yaml(data), encoding="utf-8")
        invalid = self.run_state("todos", "validate", "--file", self.todos)
        self.assertEqual(invalid.returncode, 1)
        self.assertNotIn("Traceback", invalid.stderr)

        data["tasks"][0]["status"] = "working"
        data["tasks"][0]["executor"] = {}
        data["tasks"][0]["started_at"] = data["tasks"][0]["created_at"]
        self.todos.write_text(dump_yaml(data), encoding="utf-8")
        invalid = self.run_state("todos", "validate", "--file", self.todos)
        self.assertEqual(invalid.returncode, 1)
        self.assertNotIn("Traceback", invalid.stderr)

        self.init_requirements()
        added = self.run_state(
            "requirements", "upsert", *self.session_arguments(), "--text", "校验要求",
        )
        self.assertEqual(added.returncode, 0, added.stderr)
        data = load_yaml(self.requirements)
        data["requirements"][0]["scope"] = []
        data["response_review"] = {
            "reviewed_at": "2026-11-15T00:00:00Z",
            "results": [{"requirement_id": "REQ-001", "status": [], "evidence": "无效状态"}],
        }
        self.requirements.write_text(dump_yaml(data), encoding="utf-8")
        invalid = self.run_state("requirements", "validate", "--file", self.requirements)
        self.assertEqual(invalid.returncode, 1)
        self.assertNotIn("Traceback", invalid.stderr)

    def test_concurrent_additions_do_not_lose_updates(self):
        self.init_todos()

        def add(index):
            return self.run_state(
                "todos",
                "add",
                "--file",
                self.todos,
                "--title",
                f"并发任务 {index}",
                "--acceptance-criteria",
                "记录不丢失",
            )

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(add, range(8)))
        self.assertTrue(all(result.returncode == 0 for result in results), [r.stderr for r in results])
        data = load_yaml(self.todos)
        self.assertEqual(len(data["tasks"]), 8)
        self.assertEqual(len({task["id"] for task in data["tasks"]}), 8)
        self.assertEqual(data["next_task_number"], 9)
        self.assertFalse(list(self.project.glob("..todos.yml.*.tmp")))

    def test_invalid_yaml_is_rejected_without_replacement(self):
        self.init_todos()
        original = self.todos.read_bytes()
        self.todos.write_text(original.decode("utf-8") + "unknown: true\n", encoding="utf-8")
        invalid = self.todos.read_bytes()
        result = self.run_state(
            "todos",
            "add",
            "--file",
            self.todos,
            "--title",
            "不应写入",
            "--acceptance-criteria",
            "无",
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.todos.read_bytes(), invalid)


if __name__ == "__main__":
    unittest.main()
