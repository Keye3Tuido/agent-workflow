import asyncio
import json
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import AsyncExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "lucid-workflow" / "scripts"
sys.path.insert(0, str(SCRIPTS))

try:
    from mcp import Client
except ImportError:
    Client = None


def tool_data(result):
    if result.is_error:
        raise AssertionError(result)
    return result.structured_content


async def exercise_three_clients(url, project):
    async with AsyncExitStack() as stack:
        clients = [await stack.enter_async_context(Client(url)) for _ in range(3)]
        for client in clients:
            assert client.protocol_version == "2026-07-28"
        created = await clients[0].call_tool(
            "tasks.add",
            {
                "project": project,
                "title": "单实例后台任务",
                "acceptance_criteria": ["单实例验收条件"],
            },
        )
        task_id = tool_data(created)["task_id"]
        identities = [("A", "session-a"), ("B", "session-b"), ("C", "session-c")]
        results = await asyncio.gather(
            *(
                client.call_tool(
                    "tasks.claim_next",
                    {"project": project, "agent": agent, "session_id": session},
                )
                for client, (agent, session) in zip(clients, identities)
            )
        )
        claimed = [tool_data(result)["task_id"] for result in results]
        assert [value for value in claimed if value] == [task_id]
        winner = claimed.index(task_id)
        completed = await clients[winner].call_tool(
            "tasks.complete",
            {
                "project": project,
                "agent": identities[winner][0],
                "task_id": task_id,
                "verification": "三客户端单实例验证通过",
            },
        )
        assert tool_data(completed)["ok"] is True


async def read_tasks(url, project):
    async with Client(url) as client:
        result = await client.call_tool("tasks.list", {"project": project})
        return tool_data(result)["tasks"]


@unittest.skipUnless(Client is not None, "需要安装 mcp==2.3.0")
class ServiceSingletonExperiments(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.state = self.base / "state"
        self.state.mkdir()
        self.endpoint_path = self.state / "endpoint.json"
        self.processes = []
        self.project = "singleton-project"

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
        self.temporary.cleanup()

    def start_process(self):
        process = subprocess.Popen(
            [sys.executable, "-B", "-m", "coordination.service_process", "--state-dir", str(self.state)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={"PYTHONPATH": str(SCRIPTS)},
        )
        self.processes.append(process)
        return process

    def wait_ready(self, process):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                _, error = process.communicate()
                self.fail(f"服务提前退出 {process.returncode}: {error}")
            try:
                content = json.loads(self.endpoint_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                content = None
            if content and content.get("pid") == process.pid:
                return content
            time.sleep(0.05)
        self.fail("等待服务端点超时")

    def stop_process(self, process, crash=False):
        if process.poll() is None:
            if crash:
                process.kill()
            else:
                process.terminate()
        process.wait(timeout=10)
        process.communicate()

    def test_singleton_background_keepalive_three_clients_and_crash_recovery(self):
        first = self.start_process()
        first_endpoint = self.wait_ready(first)
        duplicate = self.start_process()
        duplicate.wait(timeout=10)
        self.assertEqual(duplicate.returncode, 2)
        _, duplicate_error = duplicate.communicate()
        self.assertEqual(duplicate_error.strip(), '{"ok": false, "error": "instance_already_running"}')
        self.assertEqual(self.wait_ready(first)["instance_id"], first_endpoint["instance_id"])
        self.assertIsNone(first.poll())

        asyncio.run(exercise_three_clients(first_endpoint["url"], self.project))
        self.assertIsNone(first.poll(), "客户端退出后后台服务不应退出")
        tasks = asyncio.run(read_tasks(first_endpoint["url"], self.project))
        self.assertEqual(tasks[0]["status"], "done")

        self.stop_process(first, crash=True)
        replacement = self.start_process()
        replacement_endpoint = self.wait_ready(replacement)
        self.assertNotEqual(replacement_endpoint["instance_id"], first_endpoint["instance_id"])
        self.assertNotEqual(replacement_endpoint["pid"], first_endpoint["pid"])
        tasks = asyncio.run(read_tasks(replacement_endpoint["url"], self.project))
        self.assertEqual(tasks[0]["status"], "done")
        self.assertEqual(tasks[0]["verification"], "三客户端单实例验证通过")
        self.stop_process(replacement)

    def test_simultaneous_start_creates_only_one_service(self):
        first = self.start_process()
        second = self.start_process()
        deadline = time.monotonic() + 15
        winner = None
        while time.monotonic() < deadline:
            try:
                content = json.loads(self.endpoint_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                content = None
            if content:
                candidate = next(
                    (process for process in (first, second) if process.pid == content.get("pid")),
                    None,
                )
                if candidate and candidate.poll() is None:
                    winner = candidate
            if winner and any(process.poll() is not None for process in (first, second) if process is not winner):
                break
            if first.poll() is not None and second.poll() is not None:
                self.fail("并发启动的两个服务都退出了")
            time.sleep(0.05)
        self.assertIsNotNone(winner, "并发启动没有产生唯一服务")
        loser = second if winner is first else first
        self.assertEqual(loser.wait(timeout=10), 2)
        loser.communicate()
        self.wait_ready(winner)
        asyncio.run(read_tasks(json.loads(self.endpoint_path.read_text())["url"], self.project))
        self.stop_process(winner)


if __name__ == "__main__":
    unittest.main()
