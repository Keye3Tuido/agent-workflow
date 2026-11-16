import json
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "lucid-workflow" / "scripts"
SCRIPT = SCRIPTS / "session_questions.py"
sys.path.insert(0, str(SCRIPTS))

from yaml_common import dump_yaml, load_yaml


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

    def ask(self, question="采用哪个验收口径？", reason="影响验收结果", kind="decision"):
        result = self.run_question(
            "ask", *self.session_arguments(),
            "--question", question, "--reason", reason, "--kind", kind,
            "--context", "当前有两种实现", "--requirement-id", "REQ-001",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["id"]

    def answer(self, question_id, answer="确认"):
        result = self.run_question(
            "answer", *self.session_arguments(), "--id", question_id, "--answer", answer,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def apply(self, question_id):
        result = self.run_question("apply", *self.session_arguments(), "--id", question_id)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_decision_lifecycle_answer_then_apply(self):
        self.init()
        question_id = self.ask()
        self.answer(question_id, "以用户验收条件为准")
        data = load_yaml(self.questions)
        self.assertEqual(data["questions"][0]["status"], "answered")
        self.assertEqual(data["questions"][0]["kind"], "decision")
        self.assertNotIn("applied_at", data["questions"][0])
        active = self.run_question("list", "--file", self.questions, "--active")
        self.assertEqual(json.loads(active.stdout)["count"], 1)
        self.apply(question_id)
        data = load_yaml(self.questions)
        self.assertIn("applied_at", data["questions"][0])
        active = self.run_question("list", "--file", self.questions, "--active")
        self.assertEqual(json.loads(active.stdout)["count"], 0)
        validated = self.run_question("validate", "--file", self.questions)
        self.assertEqual(validated.returncode, 0, validated.stderr)

    def test_wrong_session_duplicate_and_invalid_transition_preserve_file(self):
        self.init()
        question_id = self.ask()
        duplicate = self.run_question(
            "ask", *self.session_arguments(),
            "--question", "采用哪个验收口径？", "--reason", "重复问题", "--kind", "decision",
        )
        self.assertEqual(duplicate.returncode, 1)
        before = self.questions.read_bytes()
        wrong = self.run_question(
            "answer", *self.session_arguments("other-session"),
            "--id", question_id, "--answer", "不应写入",
        )
        self.assertEqual(wrong.returncode, 1)
        self.assertEqual(self.questions.read_bytes(), before)
        self.answer(question_id)
        self.apply(question_id)
        before = self.questions.read_bytes()
        cancelled = self.run_question("cancel", *self.session_arguments(), "--id", question_id)
        self.assertEqual(cancelled.returncode, 1)
        self.assertEqual(self.questions.read_bytes(), before)

    def test_context_answers_are_reusable_revisable_and_not_applied(self):
        self.init()
        question_id = self.ask("项目中的订单指什么？", "后续推理需要统一术语", kind="context")
        self.answer(question_id, "订单是用户提交的购买请求")
        applied = self.run_question("apply", *self.session_arguments(), "--id", question_id)
        self.assertEqual(applied.returncode, 1)
        self.answer(question_id, "订单是已确认的购买请求")
        data = load_yaml(self.questions)
        self.assertEqual(data["questions"][0]["answer"], "订单是已确认的购买请求")
        self.assertNotIn("applied_at", data["questions"][0])
        contexts = self.run_question(
            "list", "--file", self.questions, "--kind", "context", "--status", "answered",
        )
        self.assertEqual(json.loads(contexts.stdout)["count"], 1)
        cancelled = self.run_question("cancel", *self.session_arguments(), "--id", question_id)
        self.assertEqual(cancelled.returncode, 0, cancelled.stderr)
        data = load_yaml(self.questions)
        self.assertEqual(data["questions"][0]["status"], "cancelled")
        self.assertNotIn("answer", data["questions"][0])

    def test_concurrent_questions_have_unique_ids(self):
        self.init()

        def add(index):
            return self.run_question(
                "ask", *self.session_arguments(),
                "--question", f"需要确认的问题 {index}", "--reason", "并发核验", "--kind", "decision",
            )

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(add, range(8)))
        self.assertTrue(all(result.returncode == 0 for result in results), [result.stderr for result in results])
        data = load_yaml(self.questions)
        self.assertEqual(len(data["questions"]), 8)
        self.assertEqual(len({item["id"] for item in data["questions"]}), 8)
        self.assertEqual(data["next_question_number"], 9)

    def test_closed_retention_preserves_open_decisions_and_context(self):
        self.init()
        context_id = self.ask("可复用背景", "后续推理需要", kind="context")
        self.answer(context_id, "长期有效的会话背景")
        for index in range(25):
            question_id = self.ask(f"决策 {index}", "需要落实")
            self.answer(question_id)
            self.apply(question_id)
        open_id = self.ask("仍待回答的问题", "不能自动清理")
        data = load_yaml(self.questions)
        closed = [item for item in data["questions"] if item.get("applied_at") or item["status"] == "cancelled"]
        self.assertEqual(len(closed), 20)
        self.assertIn(context_id, {item["id"] for item in data["questions"]})
        self.assertIn(open_id, {item["id"] for item in data["questions"]})
        self.assertTrue(any(item["id"] == context_id and item["answer"] == "长期有效的会话背景" for item in data["questions"]))
        validated = self.run_question("validate", "--file", self.questions)
        self.assertEqual(validated.returncode, 0, validated.stderr)

    def test_legacy_record_without_kind_is_treated_as_context(self):
        self.init()
        legacy_id = self.ask("旧记录", "兼容验证", kind="context")
        self.answer(legacy_id, "需要保留的背景")
        data = load_yaml(self.questions)
        data["questions"][0].pop("kind")
        self.questions.write_text(dump_yaml(data), encoding="utf-8")
        validated = self.run_question("validate", "--file", self.questions)
        self.assertEqual(validated.returncode, 0, validated.stderr)
        self.ask("新问题", "继续工作")
        data = load_yaml(self.questions)
        self.assertIn(legacy_id, {item["id"] for item in data["questions"]})
        self.assertNotIn("kind", data["questions"][0])

    def test_question_file_cannot_be_inside_project(self):
        inside = self.run_question(
            "init", "--file", self.project / "questions.yml",
            "--session-id", "session-test", "--project-root", self.project,
        )
        self.assertEqual(inside.returncode, 1)
        self.assertFalse((self.project / "questions.yml").exists())


if __name__ == "__main__":
    unittest.main()
