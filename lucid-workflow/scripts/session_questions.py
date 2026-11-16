#!/usr/bin/env python3
"""Persist concise user questions and answers in the session directory."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

from yaml_common import (
    YamlError,
    atomic_write_yaml,
    file_lock,
    is_timestamp,
    load_yaml,
    nonempty_string,
    require_keys,
    utc_now,
)

QUESTION_ID = re.compile(r"^Q-[0-9]{3,}$")
STATUSES = {"open", "answered", "cancelled"}
KINDS = {"decision", "context"}
MAX_CLOSED_QUESTIONS = 20
REQUIRED = {"id", "question", "reason", "status", "created_at", "updated_at"}
OPTIONAL = {
    "kind", "context", "requirement_ids", "answer", "answered_at",
    "applied_at", "cancelled_at",
}


class QuestionError(ValueError):
    pass


def question_kind(question: dict[str, Any]) -> str:
    # Records created before kinds were introduced are retained as context.
    return question.get("kind", "context")


def is_closed(question: dict[str, Any]) -> bool:
    return question["status"] == "cancelled" or bool(question.get("applied_at"))


def prune_closed(data: Any) -> None:
    closed = [
        (index, question) for index, question in enumerate(data["questions"])
        if is_closed(question)
    ]
    if len(closed) <= MAX_CLOSED_QUESTIONS:
        return
    closed.sort(key=lambda pair: (
        pair[1].get("applied_at") or pair[1].get("cancelled_at") or "",
        pair[0],
    ))
    removed = {question["id"] for _, question in closed[:-MAX_CLOSED_QUESTIONS]}
    data["questions"] = [question for question in data["questions"] if question["id"] not in removed]


def validate_document(path: Path, data: Any) -> list[str]:
    errors = require_keys(
        data,
        {"schema_version", "session", "next_question_number", "questions"},
        context="questions",
    )
    if errors:
        return errors
    if data.get("schema_version") != "1.0":
        errors.append("schema_version 必须是字符串 '1.0'")
    session = data.get("session")
    errors.extend(require_keys(session, {"id", "project_root", "created_at", "updated_at"}, context="session"))
    if isinstance(session, dict):
        if not nonempty_string(session.get("id")):
            errors.append("session.id 必须是非空字符串")
        root = session.get("project_root")
        if not nonempty_string(root) or not os.path.isabs(root):
            errors.append("session.project_root 必须是绝对路径")
        for key in ("created_at", "updated_at"):
            if not is_timestamp(session.get(key)):
                errors.append(f"session.{key} 必须是带时区时间")
    next_number = data.get("next_question_number")
    if isinstance(next_number, bool) or not isinstance(next_number, int) or next_number < 1:
        errors.append("next_question_number 必须是正整数")
    questions = data.get("questions")
    if not isinstance(questions, list):
        errors.append("questions 必须是列表")
        questions = []
    ids = []
    for index, question in enumerate(questions):
        context_name = f"questions[{index}]"
        errors.extend(require_keys(question, REQUIRED, OPTIONAL, context_name))
        if not isinstance(question, dict):
            continue
        question_id = question.get("id")
        if not isinstance(question_id, str) or not QUESTION_ID.fullmatch(question_id):
            errors.append(f"{context_name}.id 格式无效")
        else:
            ids.append(question_id)
        for key in ("question", "reason"):
            if not nonempty_string(question.get(key)):
                errors.append(f"{context_name}.{key} 必须是非空字符串")
        for key in ("created_at", "updated_at"):
            if not is_timestamp(question.get(key)):
                errors.append(f"{context_name}.{key} 必须是带时区时间")
        status = question.get("status")
        if not isinstance(status, str) or status not in STATUSES:
            errors.append(f"{context_name}.status 无效")
        kind = question_kind(question)
        if kind not in KINDS:
            errors.append(f"{context_name}.kind 无效")
        if "context" in question and not nonempty_string(question["context"]):
            errors.append(f"{context_name}.context 必须是非空字符串")
        requirement_ids = question.get("requirement_ids", [])
        if not isinstance(requirement_ids, list) or not all(
            isinstance(value, str) and re.fullmatch(r"REQ-[0-9]{3,}", value)
            for value in requirement_ids
        ):
            errors.append(f"{context_name}.requirement_ids 必须是要求 ID 列表")
        if status == "open":
            for key in ("answer", "answered_at", "applied_at", "cancelled_at"):
                if key in question:
                    errors.append(f"{context_name} open 应省略 {key}")
        elif status == "answered":
            if not nonempty_string(question.get("answer")) or not is_timestamp(question.get("answered_at")):
                errors.append(f"{context_name} answered 必须有 answer 和 answered_at")
            if "cancelled_at" in question:
                errors.append(f"{context_name} answered 应省略 cancelled_at")
            if "applied_at" in question:
                if kind != "decision" or not is_timestamp(question["applied_at"]):
                    errors.append(f"{context_name}.applied_at 仅用于 decision 且必须是带时区时间")
        elif status == "cancelled":
            if not is_timestamp(question.get("cancelled_at")):
                errors.append(f"{context_name} cancelled 必须有 cancelled_at")
            for key in ("answer", "answered_at", "applied_at"):
                if key in question:
                    errors.append(f"{context_name} cancelled 应省略 {key}")
    if len(ids) != len(set(ids)):
        errors.append("questions 含重复 ID")
    if isinstance(next_number, int) and not isinstance(next_number, bool):
        used = [int(value.split("-")[1]) for value in ids]
        if used and next_number <= max(used):
            errors.append("next_question_number 必须大于所有已用编号")
    if isinstance(session, dict):
        root_value = session.get("project_root")
        if nonempty_string(root_value) and os.path.isabs(root_value):
            root = Path(root_value).resolve()
            resolved_file = path.resolve()
            if resolved_file == root or root in resolved_file.parents:
                errors.append("questions.yml 不得位于项目目录内")
    return errors


def load_valid(path: Path) -> Any:
    data = load_yaml(path)
    errors = validate_document(path, data)
    if errors:
        raise QuestionError("; ".join(errors))
    return data


def require_session(data: Any, arguments: argparse.Namespace) -> None:
    expected_root = str(arguments.project_root.resolve())
    if data["session"]["id"] != arguments.session_id:
        raise QuestionError("会话 ID 与 questions.yml 不匹配")
    if data["session"]["project_root"] != expected_root:
        raise QuestionError("项目根目录与 questions.yml 不匹配")


def find_question(data: Any, question_id: str) -> dict[str, Any]:
    for question in data["questions"]:
        if question["id"] == question_id:
            return question
    raise QuestionError(f"找不到问题: {question_id}")


def mutate(arguments: argparse.Namespace, operation: Callable[[Any], Any]) -> Any:
    path = arguments.file
    with file_lock(path):
        data = load_valid(path)
        require_session(data, arguments)
        result = operation(data)
        prune_closed(data)
        errors = validate_document(path, data)
        if errors:
            raise QuestionError("; ".join(errors))
        atomic_write_yaml(path, data)
    return result


def initialize(arguments: argparse.Namespace) -> dict[str, Any]:
    path = arguments.file.resolve()
    root = arguments.project_root.resolve()
    if path == root or root in path.parents:
        raise QuestionError("questions.yml 不得位于项目目录内")
    with file_lock(path):
        if path.exists():
            raise QuestionError(f"文件已存在: {path}")
        now = utc_now()
        data = {
            "schema_version": "1.0",
            "session": {
                "id": arguments.session_id,
                "project_root": str(root),
                "created_at": now,
                "updated_at": now,
            },
            "next_question_number": 1,
            "questions": [],
        }
        errors = validate_document(path, data)
        if errors:
            raise QuestionError("; ".join(errors))
        atomic_write_yaml(path, data)
    return {"file": str(path)}


def ask(arguments: argparse.Namespace) -> dict[str, Any]:
    def operation(data: Any) -> Any:
        if any(item["question"] == arguments.question and item["status"] == "open" for item in data["questions"]):
            raise QuestionError("相同的未解决问题已存在")
        number = data["next_question_number"]
        question_id = f"Q-{number:03d}"
        now = utc_now()
        question = {
            "id": question_id,
            "question": arguments.question,
            "reason": arguments.reason,
            "kind": arguments.kind,
            "status": "open",
            "created_at": now,
            "updated_at": now,
        }
        if arguments.context:
            question["context"] = arguments.context
        if arguments.requirement_id:
            question["requirement_ids"] = arguments.requirement_id
        data["questions"].append(question)
        data["next_question_number"] = number + 1
        data["session"]["updated_at"] = now
        return {"id": question_id, "status": "open", "kind": arguments.kind}

    return mutate(arguments, operation)


def answer(arguments: argparse.Namespace) -> dict[str, Any]:
    def operation(data: Any) -> Any:
        question = find_question(data, arguments.id)
        if question["status"] == "cancelled" or question.get("applied_at"):
            raise QuestionError("已取消或已落实的问题不能修改回答")
        now = utc_now()
        question["status"] = "answered"
        question["answer"] = arguments.answer
        question["answered_at"] = now
        question["updated_at"] = now
        data["session"]["updated_at"] = now
        return {"id": arguments.id, "status": "answered"}

    return mutate(arguments, operation)


def apply(arguments: argparse.Namespace) -> dict[str, Any]:
    def operation(data: Any) -> Any:
        question = find_question(data, arguments.id)
        if question["status"] != "answered" or question_kind(question) != "decision":
            raise QuestionError("只有已回答的 decision 问题可以标记落实")
        if question.get("applied_at"):
            raise QuestionError("该问题已经落实")
        now = utc_now()
        question["applied_at"] = now
        question["updated_at"] = now
        data["session"]["updated_at"] = now
        return {"id": arguments.id, "status": "answered", "applied": True}

    return mutate(arguments, operation)


def cancel(arguments: argparse.Namespace) -> dict[str, Any]:
    def operation(data: Any) -> Any:
        question = find_question(data, arguments.id)
        if question["status"] == "cancelled" or question.get("applied_at"):
            raise QuestionError("已取消或已落实的问题不能取消")
        now = utc_now()
        for key in ("answer", "answered_at", "applied_at"):
            question.pop(key, None)
        question["status"] = "cancelled"
        question["cancelled_at"] = now
        question["updated_at"] = now
        data["session"]["updated_at"] = now
        return {"id": arguments.id, "status": "cancelled"}

    return mutate(arguments, operation)


def list_questions(arguments: argparse.Namespace) -> dict[str, Any]:
    data = load_valid(arguments.file)
    questions = []
    for item in data["questions"]:
        if arguments.status is not None and item["status"] != arguments.status:
            continue
        if arguments.kind is not None and question_kind(item) != arguments.kind:
            continue
        if arguments.active and not (
            item["status"] == "open"
            or (item["status"] == "answered" and question_kind(item) == "decision" and not item.get("applied_at"))
        ):
            continue
        questions.append(item)
    return {"count": len(questions), "questions": questions}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    def session_arguments(subparser):
        subparser.add_argument("--file", required=True, type=Path)
        subparser.add_argument("--session-id", required=True)
        subparser.add_argument("--project-root", required=True, type=Path)

    init_parser = subcommands.add_parser("init")
    session_arguments(init_parser)
    init_parser.set_defaults(handler=initialize)

    ask_parser = subcommands.add_parser("ask")
    session_arguments(ask_parser)
    ask_parser.add_argument("--question", required=True)
    ask_parser.add_argument("--reason", required=True)
    ask_parser.add_argument("--kind", required=True, choices=sorted(KINDS))
    ask_parser.add_argument("--context")
    ask_parser.add_argument("--requirement-id", action="append")
    ask_parser.set_defaults(handler=ask)

    answer_parser = subcommands.add_parser("answer")
    session_arguments(answer_parser)
    answer_parser.add_argument("--id", required=True)
    answer_parser.add_argument("--answer", required=True)
    answer_parser.set_defaults(handler=answer)

    apply_parser = subcommands.add_parser("apply")
    session_arguments(apply_parser)
    apply_parser.add_argument("--id", required=True)
    apply_parser.set_defaults(handler=apply)

    cancel_parser = subcommands.add_parser("cancel")
    session_arguments(cancel_parser)
    cancel_parser.add_argument("--id", required=True)
    cancel_parser.set_defaults(handler=cancel)

    list_parser = subcommands.add_parser("list")
    list_parser.add_argument("--file", required=True, type=Path)
    list_parser.add_argument("--status", choices=sorted(STATUSES))
    list_parser.add_argument("--kind", choices=sorted(KINDS))
    list_parser.add_argument("--active", action="store_true")
    list_parser.set_defaults(handler=list_questions)

    validate_parser = subcommands.add_parser("validate")
    validate_parser.add_argument("--file", required=True, type=Path)
    validate_parser.set_defaults(handler=lambda args: {"valid": load_valid(args.file) is not None})
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    try:
        result = arguments.handler(arguments)
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return 0
    except QuestionError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    except (YamlError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
