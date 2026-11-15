import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "agent-workflow" / "scripts"
SCRIPT = SCRIPTS / "state_files.py"
sys.path.insert(0, str(SCRIPTS))

from yaml_common import dump_yaml, load_yaml


class DependencyAndAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.project = self.base / "project"
        self.session = self.base / "session"
        self.project.mkdir()
        self.session.mkdir()
        self.todos = self.project / ".todos.yml"
        self.todos_audit = self.project / ".agent-workflow-audit.jsonl"

    def tearDown(self):
        self.temporary.cleanup()

    def run_state(self, *arguments, session=None):
        environment = os.environ.copy()
        if session is not None:
            environment["AGENT_WORKFLOW_SESSION_ID"] = session
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), *map(str, arguments)],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )

    def require_ok(self, *arguments, session=None):
        completed = self.run_state(*arguments, session=session)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def init_todos(self):
        self.require_ok("todos", "init", "--file", self.todos)

    def add_task(self, title, *extra):
        return self.require_ok(
            "todos", "add", "--file", self.todos,
            "--title", title, "--acceptance-criteria", "验证通过", *extra,
        )["id"]

    def transition(self, task_id, status, *extra, session=None):
        return self.require_ok(
            "todos", "transition", "--file", self.todos,
            "--id", task_id, "--to", status, *extra,
            session=session,
        )

    def events(self, path):
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_dependency_blocks_start_and_failed_attempt_has_no_commit(self):
        self.init_todos()
        prerequisite = self.add_task("前置任务")
        dependent = self.add_task("后续任务", "--dependency", prerequisite)
        before = self.todos.read_bytes()
        blocked = self.run_state(
            "todos", "transition", "--file", self.todos,
            "--id", dependent, "--to", "working", "--executor", "agent-b",
            session="session-b",
        )
        self.assertEqual(blocked.returncode, 1)
        self.assertIn("dependency_not_complete", blocked.stderr)
        self.assertEqual(self.todos.read_bytes(), before)

        events = self.events(self.todos_audit)
        failed = [event for event in events if event["event"] == "failed"]
        self.assertEqual(len(failed), 1)
        failure = failed[0]
        self.assertEqual(failure["target_id"], dependent)
        self.assertEqual(failure["from"], "todo")
        self.assertEqual(failure["to"], "working")
        self.assertEqual(failure["actor"], "agent-b")
        self.assertEqual(failure["session_id"], "session-b")
        self.assertTrue(any(
            event["event"] == "prepared" and event["operation_id"] == failure["operation_id"]
            for event in events
        ))
        self.assertFalse(any(
            event["event"] == "committed" and event["operation_id"] == failure["operation_id"]
            for event in events
        ))

        self.transition(prerequisite, "working", "--executor", "agent-a", session="session-a")
        self.transition(prerequisite, "done", "--verification", "前置测试通过", session="session-a")
        self.transition(dependent, "working", "--executor", "agent-b", session="session-b")
        data = load_yaml(self.todos)
        self.assertEqual(data["tasks"][1]["status"], "working")
        committed = [event for event in self.events(self.todos_audit) if event["event"] == "committed"]
        self.assertEqual(committed[-1]["state_sha256"], hashlib.sha256(self.todos.read_bytes()).hexdigest())
        self.assertEqual(committed[-1]["session_id"], "session-b")

    def test_cycle_is_rejected_and_validate_does_not_append_audit(self):
        self.init_todos()
        first = self.add_task("第一项")
        second = self.add_task("第二项", "--dependency", first)
        data = load_yaml(self.todos)
        data["tasks"][0]["dependencies"] = [second]
        self.todos.write_text(dump_yaml(data), encoding="utf-8")
        before = self.todos.read_bytes()
        audit_before = self.todos_audit.read_bytes()
        result = self.run_state("todos", "validate", "--file", self.todos)
        self.assertEqual(result.returncode, 1)
        self.assertIn("dependency_cycle", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(self.todos.read_bytes(), before)
        self.assertEqual(self.todos_audit.read_bytes(), audit_before)

    def test_malformed_dependencies_do_not_crash_cycle_validation(self):
        self.init_todos()
        self.add_task("异常依赖")
        data = load_yaml(self.todos)
        data["tasks"][0]["dependencies"] = None
        self.todos.write_text(dump_yaml(data), encoding="utf-8")
        result = self.run_state("todos", "validate", "--file", self.todos)
        self.assertEqual(result.returncode, 1)
        self.assertIn("dependencies 必须是字符串列表", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_remove_dependency_is_rejected_and_clean_accepted_removes_edge(self):
        self.init_todos()
        prerequisite = self.add_task("被依赖任务")
        dependent = self.add_task("依赖任务", "--dependency", prerequisite)
        before = self.todos.read_bytes()
        removed = self.run_state("todos", "remove", "--file", self.todos, "--id", prerequisite)
        self.assertEqual(removed.returncode, 1)
        self.assertIn("dependency_in_use", removed.stderr)
        self.assertIn(dependent, removed.stderr)
        self.assertEqual(self.todos.read_bytes(), before)

        self.transition(prerequisite, "working", "--executor", "agent-a")
        self.transition(prerequisite, "done", "--verification", "测试通过")
        self.transition(prerequisite, "accepted", "--acceptance-evidence", "用户验收通过")
        self.require_ok("todos", "clean-accepted", "--file", self.todos)
        data = load_yaml(self.todos)
        self.assertEqual([task["id"] for task in data["tasks"]], [dependent])
        self.assertNotIn("dependencies", data["tasks"][0])

    def test_requirements_audit_is_isolated_and_omits_requirement_text(self):
        session_a = self.base / "session-a"
        session_b = self.base / "session-b"
        session_a.mkdir()
        session_b.mkdir()
        requirements_a = session_a / "requirements.yml"
        requirements_b = session_b / "requirements.yml"
        audit_a = session_a / "requirements-audit.jsonl"
        audit_b = session_b / "requirements-audit.jsonl"

        def session_arguments(path, session_id):
            return (
                "--file", path, "--session-id", session_id,
                "--project-root", self.project,
            )

        text_a = "会话甲的私密要求正文"
        text_b = "会话乙的私密要求正文"
        for path, session_id, text in (
            (requirements_a, "session-a", text_a),
            (requirements_b, "session-b", text_b),
        ):
            self.require_ok("requirements", "init", *session_arguments(path, session_id))
            self.require_ok("requirements", "upsert", *session_arguments(path, session_id), "--text", text)

        raw_a = audit_a.read_text(encoding="utf-8")
        raw_b = audit_b.read_text(encoding="utf-8")
        self.assertNotIn(text_a, raw_a)
        self.assertNotIn(text_b, raw_a)
        self.assertNotIn(text_a, raw_b)
        self.assertNotIn(text_b, raw_b)
        events_a = self.events(audit_a)
        events_b = self.events(audit_b)
        self.assertTrue(all(event["session_id"] == "session-a" for event in events_a))
        self.assertTrue(all(event["session_id"] == "session-b" for event in events_b))
        self.assertTrue(any(event["event"] == "committed" and event["operation"] == "upsert" for event in events_a))
        self.assertTrue(any(event["event"] == "committed" and event["operation"] == "upsert" for event in events_b))

        audit_a_before = audit_a.read_bytes()
        audit_b_before = audit_b.read_bytes()
        for path in (requirements_a, requirements_b):
            validated = self.run_state("requirements", "validate", "--file", path)
            self.assertEqual(validated.returncode, 0, validated.stderr)
        self.assertEqual(audit_a.read_bytes(), audit_a_before)
        self.assertEqual(audit_b.read_bytes(), audit_b_before)

        secret = "不应写入审计的新要求正文"
        requirements_before = requirements_a.read_bytes()
        wrong_session = self.run_state(
            "requirements", "upsert", *session_arguments(requirements_a, "session-b"),
            "--text", secret,
        )
        self.assertEqual(wrong_session.returncode, 1)
        self.assertEqual(requirements_a.read_bytes(), requirements_before)
        self.assertNotIn(secret, audit_a.read_text(encoding="utf-8"))
        self.assertEqual(audit_b.read_bytes(), audit_b_before)
        failure = [event for event in self.events(audit_a) if event["event"] == "failed"][-1]
        self.assertEqual(failure["session_id"], "session-b")
        self.assertFalse(any(
            event["event"] == "committed" and event["operation_id"] == failure["operation_id"]
            for event in self.events(audit_a)
        ))

    def test_failed_audit_does_not_copy_yaml_error_source(self):
        requirements = self.session / "requirements.yml"
        arguments = (
            "--file", requirements, "--session-id", "session-test",
            "--project-root", self.project,
        )
        self.require_ok("requirements", "init", *arguments)
        marker = "SECRET_REQUIREMENT_BODY"
        requirements.write_text(
            f'schema_version: "1.0"\n{marker}: "unterminated\n',
            encoding="utf-8",
        )
        completed = self.run_state("requirements", "upsert", *arguments, "--text", "新要求")
        self.assertEqual(completed.returncode, 2)
        audit_path = self.session / "requirements-audit.jsonl"
        raw_audit = audit_path.read_text(encoding="utf-8")
        self.assertNotIn(marker, raw_audit)
        failure = [event for event in self.events(audit_path) if event["event"] == "failed"][-1]
        self.assertEqual(failure["error_type"], "YamlError")
        self.assertEqual(failure["error"], "状态或数据校验失败")

    def test_audit_compacts_to_recent_complete_operations(self):
        self.init_todos()
        for index in range(99):
            self.add_task(f"滚动审计任务 {index}")
        events = self.events(self.todos_audit)
        operation_ids = list(dict.fromkeys(event["operation_id"] for event in events))
        self.assertEqual(len(operation_ids), 100)
        self.assertEqual(len(events), 200)

        self.add_task("触发审计压缩")
        events = self.events(self.todos_audit)
        operation_ids = list(dict.fromkeys(event["operation_id"] for event in events))
        self.assertEqual(len(operation_ids), 50)
        self.assertEqual(len(events), 100)
        for operation_id in operation_ids:
            self.assertEqual(
                [event["event"] for event in events if event["operation_id"] == operation_id],
                ["prepared", "committed"],
            )
        committed = [event for event in events if event["event"] == "committed"]
        self.assertEqual(committed[-1]["target_id"], "TASK-100")
        self.assertEqual(len(load_yaml(self.todos)["tasks"]), 100)

        failed = self.run_state(
            "todos", "transition", "--file", self.todos,
            "--id", "TASK-100", "--to", "done", "--verification", "非法迁移",
        )
        self.assertEqual(failed.returncode, 1)
        events = self.events(self.todos_audit)
        operation_ids = list(dict.fromkeys(event["operation_id"] for event in events))
        self.assertEqual(len(operation_ids), 51)
        self.assertEqual(len(events), 102)
        self.assertEqual([event["event"] for event in events[-2:]], ["prepared", "failed"])
        self.assertEqual(events[-1]["operation_id"], events[-2]["operation_id"])


if __name__ == "__main__":
    unittest.main()
