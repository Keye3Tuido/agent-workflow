import json
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "agent-workflow" / "scripts"
SCRIPT = SCRIPTS / "session_questions.py"
sys.path.insert(0, str(SCRIPTS))

from yaml_common import load_yaml


class SessionQuestionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.project = self.base / "project"
        self.session = self.base / "session"
        self.project.mkdir()
        self.session.mkdir()
        self.questions = self.session / "questions.yml"

    def tearDown(self):
        self.temporary.cleanup()

    def run_question(self, *arguments):
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), *map(str, arguments)],
            capture_output=True,
            text=True,
            check=False,
        )

    def session_arguments(self, session_id="session-test"):
        return (
            "--file", self.questions,
            "--session-id", session_id,
            "--project-root", self.project,
        )

    def init(self):
        result = self.run_question("init", *self.session_arguments())
        self.assertEqual(result.returncode, 0, result.stderr)

    def ask(self, question="采用哪个验收口径？", reason="影响验收结果"):
        result = self.run_question(
            "ask", *self.session_arguments(),
            "--question", question, "--reason", reason,
            "--context", "当前有两种实现", "--requirement-id", "REQ-001",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["id"]

    def test_question_lifecycle_and_recovery(self):
        self.init()
        question_id = self.ask()
        opened = self.run_question("list", "--file", self.questions, "--status", "open")
        self.assertEqual(json.loads(opened.stdout)["count"], 1)
        answered = self.run_question(
            "answer", *self.session_arguments(), "--id", question_id,
            "--answer", "以用户验收条件为准",
        )
        self.assertEqual(answered.returncode, 0, answered.stderr)
        data = load_yaml(self.questions)
        self.assertEqual(data["questions"][0]["status"], "answered")
        self.assertEqual(data["questions"][0]["answer"], "以用户验收条件为准")
        self.assertEqual(data["questions"][0]["requirement_ids"], ["REQ-001"])
        opened = self.run_question("list", "--file", self.questions, "--status", "open")
        self.assertEqual(json.loads(opened.stdout)["count"], 0)
        validated = self.run_question("validate", "--file", self.questions)
        self.assertEqual(validated.returncode, 0, validated.stderr)

    def test_wrong_session_duplicate_and_invalid_transition_preserve_file(self):
        self.init()
        question_id = self.ask()
        duplicate = self.run_question(
            "ask", *self.session_arguments(),
            "--question", "采用哪个验收口径？", "--reason", "重复问题",
        )
        self.assertEqual(duplicate.returncode, 1)
        before = self.questions.read_bytes()
        wrong = self.run_question(
            "answer", *self.session_arguments("other-session"),
            "--id", question_id, "--answer", "不应写入",
        )
        self.assertEqual(wrong.returncode, 1)
        self.assertEqual(self.questions.read_bytes(), before)
        answered = self.run_question(
            "answer", *self.session_arguments(), "--id", question_id, "--answer", "确认",
        )
        self.assertEqual(answered.returncode, 0, answered.stderr)
        before = self.questions.read_bytes()
        cancelled = self.run_question("cancel", *self.session_arguments(), "--id", question_id)
        self.assertEqual(cancelled.returncode, 1)
        self.assertEqual(self.questions.read_bytes(), before)

    def test_concurrent_questions_have_unique_ids(self):
        self.init()

        def add(index):
            return self.run_question(
                "ask", *self.session_arguments(),
                "--question", f"需要确认的问题 {index}", "--reason", "并发核验",
            )

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(add, range(8)))
        self.assertTrue(all(result.returncode == 0 for result in results), [result.stderr for result in results])
        data = load_yaml(self.questions)
        self.assertEqual(len(data["questions"]), 8)
        self.assertEqual(len({item["id"] for item in data["questions"]}), 8)
        self.assertEqual(data["next_question_number"], 9)

    def test_question_file_cannot_be_inside_project(self):
        inside = self.run_question(
            "init", "--file", self.project / "questions.yml",
            "--session-id", "session-test", "--project-root", self.project,
        )
        self.assertEqual(inside.returncode, 1)
        self.assertFalse((self.project / "questions.yml").exists())


if __name__ == "__main__":
    unittest.main()
