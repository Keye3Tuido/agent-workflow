import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "agent-workflow" / "scripts" / "state_files.py"


class MultiAgentWorkflowTests(unittest.TestCase):
    def test_two_agents_share_tasks_without_mixing_requirements(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / "project"
            session_a = base / "session-a"
            session_b = base / "session-b"
            project.mkdir()
            session_a.mkdir()
            session_b.mkdir()
            todos = project / ".todos.yml"
            requirements_a = session_a / "requirements.yml"
            requirements_b = session_b / "requirements.yml"
            operations = []
            checks = []

            def run(agent, *arguments):
                environment = os.environ.copy()
                environment["AGENT_WORKFLOW_SESSION_ID"] = f"session-{agent.lower()}"
                completed = subprocess.run(
                    [sys.executable, "-B", str(SCRIPT), *map(str, arguments)],
                    capture_output=True,
                    text=True,
                    check=False,
                    env=environment,
                )
                payload = json.loads(completed.stdout) if completed.stdout else {}
                operations.append({
                    "agent": agent,
                    "operation": " ".join(map(str, arguments[:2])),
                    "returncode": completed.returncode,
                    "result": payload,
                })
                return completed, payload

            def require_ok(agent, *arguments):
                completed, payload = run(agent, *arguments)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                return payload

            require_ok("A", "todos", "init", "--file", todos)
            for agent, path in (("A", requirements_a), ("B", requirements_b)):
                require_ok(
                    agent,
                    "requirements", "init", "--file", path,
                    "--session-id", f"session-{agent.lower()}",
                    "--project-root", project,
                )
            require_ok(
                "A", "requirements", "upsert", "--file", requirements_a,
                "--session-id", "session-a", "--project-root", project,
                "--text", "实现配置读取函数并验证",
            )
            require_ok(
                "B", "requirements", "upsert", "--file", requirements_b,
                "--session-id", "session-b", "--project-root", project,
                "--text", "不得修改生产代码", "--polarity", "forbidden",
            )

            def add_task(agent):
                return require_ok(
                    agent,
                    "todos", "add", "--file", todos,
                    "--title", "实现配置读取" if agent == "A" else "编写配置读取测试",
                    "--acceptance-criteria", "配置读取测试通过",
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                added = list(executor.map(add_task, ("A", "B")))
            task_ids = sorted(item["id"] for item in added)
            self.assertEqual(len(set(task_ids)), 2)
            task_a = added[0]["id"]
            task_b = added[1]["id"]
            require_ok("A", "todos", "transition", "--file", todos, "--id", task_a, "--to", "working", "--executor", "agent-a")
            require_ok("B", "todos", "transition", "--file", todos, "--id", task_b, "--to", "working", "--executor", "agent-b")
            conflict_task = require_ok(
                "B", "todos", "add", "--file", todos,
                "--title", "并行额外任务", "--acceptance-criteria", "不得干扰现有任务",
            )["id"]
            conflict, _ = run(
                "B", "todos", "transition", "--file", todos, "--id", conflict_task,
                "--to", "working", "--executor", "agent-b",
            )
            self.assertEqual(conflict.returncode, 1)

            config = project / "config.py"
            config.write_text(
                "from pathlib import Path\n\n"
                "def read_value(path, default='default'):\n"
                "    target = Path(path)\n"
                "    return target.read_text(encoding='utf-8').strip() if target.is_file() else default\n",
                encoding="utf-8",
            )
            production_hash = hashlib.sha256(config.read_bytes()).hexdigest()
            tests_dir = project / "tests"
            tests_dir.mkdir()
            (tests_dir / "test_config.py").write_text(
                "import sys\nimport unittest\nfrom pathlib import Path\n"
                f"sys.path.insert(0, {str(project)!r})\n"
                "from config import read_value\n\n"
                "class ConfigTests(unittest.TestCase):\n"
                "    def test_existing_and_missing(self):\n"
                f"        target = Path({str(project)!r}) / 'value.txt'\n"
                "        target.write_text('configured', encoding='utf-8')\n"
                "        self.assertEqual(read_value(target), 'configured')\n"
                "        self.assertEqual(read_value(target.with_suffix('.missing')), 'default')\n",
                encoding="utf-8",
            )
            self.assertEqual(hashlib.sha256(config.read_bytes()).hexdigest(), production_hash)
            test_run = subprocess.run(
                [sys.executable, "-B", "-m", "unittest", "discover", "-s", str(tests_dir)],
                capture_output=True,
                text=True,
                check=False,
            )
            operations.append({
                "agent": "A+B",
                "operation": "python unittest discover",
                "returncode": test_run.returncode,
                "result": {"tests": 1, "stderr": test_run.stderr.strip()},
            })
            self.assertEqual(test_run.returncode, 0, test_run.stderr)
            verification = "配置读取正常值和缺失默认值测试通过"
            for agent, task_id in (("A", task_a), ("B", task_b)):
                require_ok(
                    agent, "todos", "transition", "--file", todos, "--id", task_id,
                    "--to", "done", "--verification", verification,
                )
            for agent, path in (("A", requirements_a), ("B", requirements_b)):
                evidence = verification if agent == "A" else "Agent B 仅新增测试，生产文件哈希未变"
                require_ok(
                    agent, "requirements", "review", "--file", path,
                    "--session-id", f"session-{agent.lower()}", "--project-root", project,
                    "--results", json.dumps([{
                        "requirement_id": "REQ-001",
                        "status": "met",
                        "evidence": evidence,
                    }], ensure_ascii=False),
                )
            before = requirements_a.read_bytes()
            wrong_session, _ = run(
                "A", "requirements", "upsert", "--file", requirements_a,
                "--session-id", "session-b", "--project-root", project,
                "--text", "不应写入",
            )
            self.assertEqual(wrong_session.returncode, 1)
            self.assertEqual(requirements_a.read_bytes(), before)

            def read_yaml(path):
                return json.loads(json.dumps(__import__("yaml").safe_load(path.read_text(encoding="utf-8")), ensure_ascii=False))

            done_snapshot = read_yaml(todos)
            self.assertEqual(
                {task["id"]: task["status"] for task in done_snapshot["tasks"]},
                {task_a: "done", task_b: "done", conflict_task: "todo"},
            )
            review_a = read_yaml(requirements_a)
            review_b = read_yaml(requirements_b)
            self.assertEqual(review_a["requirements"][0]["text"], "实现配置读取函数并验证")
            self.assertEqual(review_b["requirements"][0]["text"], "不得修改生产代码")
            self.assertEqual(review_a["response_review"]["results"][0]["status"], "met")
            self.assertEqual(review_b["response_review"]["results"][0]["status"], "met")
            require_ok("B", "todos", "remove", "--file", todos, "--id", conflict_task)
            for agent, task_id in (("A", task_a), ("B", task_b)):
                require_ok(
                    agent, "todos", "transition", "--file", todos, "--id", task_id,
                    "--to", "accepted", "--acceptance-evidence", "模拟用户明确回复：验收通过",
                )
            accepted_snapshot = read_yaml(todos)
            self.assertTrue(all(task["status"] == "accepted" for task in accepted_snapshot["tasks"]))
            require_ok("A", "todos", "clean-accepted", "--file", todos)
            cleaned_snapshot = read_yaml(todos)
            self.assertEqual(cleaned_snapshot["tasks"], [])

            def read_jsonl(path):
                return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

            project_audit = read_jsonl(project / ".agent-workflow-audit.jsonl")
            requirements_audit_a = read_jsonl(session_a / "requirements-audit.jsonl")
            requirements_audit_b = read_jsonl(session_b / "requirements-audit.jsonl")
            committed = [event for event in project_audit if event["event"] == "committed"]
            self.assertTrue(any(
                event["operation"] == "transition" and event["target_id"] == task_a
                and event["to"] == "working" and event["actor"] == "agent-a"
                and event["session_id"] == "session-a"
                for event in committed
            ))
            self.assertTrue(any(
                event["operation"] == "transition" and event["target_id"] == task_b
                and event["to"] == "done" and event["session_id"] == "session-b"
                for event in committed
            ))
            conflict_failure = next(
                event for event in project_audit
                if event["event"] == "failed" and event.get("target_id") == conflict_task
            )
            self.assertNotIn(
                conflict_failure["operation_id"],
                {event["operation_id"] for event in committed},
            )
            self.assertTrue(any(
                event["event"] == "committed" and event["operation"] == "review"
                and event["session_id"] == "session-a"
                for event in requirements_audit_a
            ))
            self.assertTrue(any(
                event["event"] == "failed" and event["operation"] == "upsert"
                and event["session_id"] == "session-b"
                for event in requirements_audit_a
            ))
            self.assertTrue(all(event["session_id"] == "session-b" for event in requirements_audit_b))
            self.assertNotIn("不应写入", json.dumps(requirements_audit_a, ensure_ascii=False))
            checks.extend([
                "两个 Agent 并发添加任务且 ID 不冲突",
                "不同执行者可各有一个 working，同执行者第二个 working 被拒绝",
                "实际配置读取与测试通过，生产文件未被 Agent B 修改",
                "两个会话的要求和复核记录保持隔离，错误会话写入被拒绝",
                "验收后清理 accepted，未验收任务可删除",
                "共享任务和会话要求均记录 prepared、committed、failed，失败操作无 committed",
            ])
            report = {
                "schema_version": "1.0",
                "scenario": "Agent A 实现配置读取；Agent B 仅编写测试",
                "checks": checks,
                "operations": operations,
                "snapshots": {
                    "done": done_snapshot,
                    "accepted": accepted_snapshot,
                    "cleaned": cleaned_snapshot,
                    "requirements_a": review_a,
                    "requirements_b": review_b,
                    "project_audit": project_audit,
                    "requirements_audit_a": requirements_audit_a,
                    "requirements_audit_b": requirements_audit_b,
                },
            }
            report_path = os.environ.get("AGENT_WORKFLOW_EVAL_REPORT")
            if report_path:
                Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
