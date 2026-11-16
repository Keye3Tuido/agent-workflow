#!/usr/bin/env python3
"""Validate and atomically maintain session requirements and project todos."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

from state_audit import perform
from yaml_common import (
    YamlError,
    atomic_write_yaml,
    is_timestamp,
    load_yaml,
    nonempty_string,
    require_keys,
    utc_now,
)


class StateError(ValueError):
    pass


REQUIREMENT_ID = re.compile(r"^REQ-[0-9]{3,}$")
TASK_ID = re.compile(r"^TASK-[0-9]{3,}$")
REVIEW_STATUSES = {"met", "unmet", "not_applicable"}
TASK_STATUSES = {"todo", "working", "done", "accepted"}
SCOPE_TYPES = {"session", "phase", "task", "turn"}
TASK_FIELDS = {
    "id",
    "title",
    "acceptance_criteria",
    "status",
    "created_at",
    "updated_at",
    "dependencies",
    "executor",
    "started_at",
    "completed_at",
    "accepted_at",
    "verification",
    "acceptance_evidence",
    "blocked_reason",
}


def nullable_timestamp(value: Any) -> bool:
    return value is None or is_timestamp(value)


def string_list(value: Any, allow_empty: bool = True) -> bool:
    return (
        isinstance(value, list)
        and (allow_empty or bool(value))
        and all(nonempty_string(item) for item in value)
    )


def validate_requirements_data(data: Any) -> list[str]:
    errors = require_keys(
        data,
        {"schema_version", "session", "next_requirement_number", "requirements", "response_review"},
        context="requirements",
    )
    if errors:
        return errors
    if data.get("schema_version") != "1.0":
        errors.append("schema_version 必须是字符串 '1.0'")
    session = data.get("session")
    errors.extend(
        require_keys(session, {"id", "project_root", "created_at", "updated_at"}, context="session")
    )
    if isinstance(session, dict):
        if not nonempty_string(session.get("id")):
            errors.append("session.id 必须是非空字符串")
        root = session.get("project_root")
        if not nonempty_string(root) or not os.path.isabs(root):
            errors.append("session.project_root 必须是绝对路径")
        for key in ("created_at", "updated_at"):
            if not is_timestamp(session.get(key)):
                errors.append(f"session.{key} 必须是带时区时间")
    next_number = data.get("next_requirement_number")
    if isinstance(next_number, bool) or not isinstance(next_number, int) or next_number < 1:
        errors.append("next_requirement_number 必须是正整数")
    requirements = data.get("requirements")
    if not isinstance(requirements, list):
        errors.append("requirements 必须是列表")
        requirements = []
    ids = []
    optional = {"polarity", "scope", "task_ids", "source_quote", "expires_when"}
    for index, requirement in enumerate(requirements):
        context = f"requirements[{index}]"
        errors.extend(require_keys(requirement, {"id", "text"}, optional, context))
        if not isinstance(requirement, dict):
            continue
        requirement_id = requirement.get("id")
        if not isinstance(requirement_id, str) or not REQUIREMENT_ID.fullmatch(requirement_id):
            errors.append(f"{context}.id 格式无效")
        else:
            ids.append(requirement_id)
        if not nonempty_string(requirement.get("text")):
            errors.append(f"{context}.text 必须是非空字符串")
        if requirement.get("polarity", "required") not in ("required", "forbidden"):
            errors.append(f"{context}.polarity 无效")
        scope = requirement.get("scope", "session")
        if not isinstance(scope, str) or scope not in SCOPE_TYPES:
            errors.append(f"{context}.scope 无效")
        task_ids = requirement.get("task_ids", [])
        if not string_list(task_ids):
            errors.append(f"{context}.task_ids 必须是非空字符串列表或空列表")
        if scope == "session":
            if task_ids:
                errors.append(f"{context} session 范围不得包含 task_ids")
            if "expires_when" in requirement:
                errors.append(f"{context} session 范围应省略 expires_when")
        else:
            if not nonempty_string(requirement.get("expires_when")):
                errors.append(f"{context} 非 session 范围必须有 expires_when")
            if scope == "task" and not string_list(task_ids, allow_empty=False):
                errors.append(f"{context} task 范围至少需要一个 task_id")
            if scope != "task" and task_ids:
                errors.append(f"{context} 仅 task 范围可包含 task_ids")
        if "source_quote" in requirement and not nonempty_string(requirement["source_quote"]):
            errors.append(f"{context}.source_quote 必须是非空字符串")
    if len(ids) != len(set(ids)):
        errors.append("requirements 含重复 ID")
    if isinstance(next_number, int) and not isinstance(next_number, bool):
        used_numbers = [int(value.split("-")[1]) for value in ids]
        if used_numbers and next_number <= max(used_numbers):
            errors.append("next_requirement_number 必须大于所有已用编号")
    review = data.get("response_review")
    errors.extend(require_keys(review, {"results"}, {"reviewed_at"}, "response_review"))
    if isinstance(review, dict):
        reviewed_at = review.get("reviewed_at")
        if "reviewed_at" in review and not is_timestamp(reviewed_at):
            errors.append("response_review.reviewed_at 必须是带时区时间")
        results = review.get("results")
        if not isinstance(results, list):
            errors.append("response_review.results 必须是列表")
            results = []
        result_ids = []
        for index, result in enumerate(results):
            context = f"response_review.results[{index}]"
            errors.extend(
                require_keys(result, {"requirement_id", "status", "evidence"}, {"reason"}, context)
            )
            if not isinstance(result, dict):
                continue
            requirement_id = result.get("requirement_id")
            if not isinstance(requirement_id, str):
                errors.append(f"{context}.requirement_id 必须是字符串")
            else:
                result_ids.append(requirement_id)
            review_status = result.get("status")
            if not isinstance(review_status, str) or review_status not in REVIEW_STATUSES:
                errors.append(f"{context}.status 无效")
            if not nonempty_string(result.get("evidence")):
                errors.append(f"{context}.evidence 必须是非空字符串")
            if result.get("status") == "unmet":
                if not nonempty_string(result.get("reason")):
                    errors.append(f"{context} unmet 必须提供 reason")
            elif "reason" in result:
                errors.append(f"{context} 非 unmet 应省略 reason")
        if len(result_ids) != len(set(result_ids)):
            errors.append("response_review 含重复 requirement_id")
        if reviewed_at is None and results:
            errors.append("有复核结果时必须有 reviewed_at")
        if reviewed_at is not None and set(result_ids) != set(ids):
            errors.append("本轮复核必须恰好覆盖全部有效要求")
    return errors


def validate_todos_data(data: Any) -> list[str]:
    errors = require_keys(
        data, {"schema_version", "updated_at", "next_task_number", "tasks"}, context="todos"
    )
    if errors:
        return errors
    if data.get("schema_version") != "1.0":
        errors.append("schema_version 必须是字符串 '1.0'")
    if not is_timestamp(data.get("updated_at")):
        errors.append("updated_at 必须是带时区时间")
    next_number = data.get("next_task_number")
    if isinstance(next_number, bool) or not isinstance(next_number, int) or next_number < 1:
        errors.append("next_task_number 必须是正整数")
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        errors.append("tasks 必须是列表")
        tasks = []
    required = {"id", "title", "acceptance_criteria", "status", "created_at", "updated_at"}
    optional = TASK_FIELDS - required
    ids = []
    for index, task in enumerate(tasks):
        context = f"tasks[{index}]"
        errors.extend(require_keys(task, required, optional, context))
        if not isinstance(task, dict):
            continue
        task_id = task.get("id")
        if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
            errors.append(f"{context}.id 格式无效")
        else:
            ids.append(task_id)
        if not nonempty_string(task.get("title")):
            errors.append(f"{context}.title 必须是非空字符串")
        if not string_list(task.get("acceptance_criteria"), allow_empty=False):
            errors.append(f"{context}.acceptance_criteria 至少需要一个非空条件")
        status = task.get("status")
        if not isinstance(status, str) or status not in TASK_STATUSES:
            errors.append(f"{context}.status 无效")
        for key in ("created_at", "updated_at"):
            if not is_timestamp(task.get(key)):
                errors.append(f"{context}.{key} 必须是带时区时间")
        for key in ("started_at", "completed_at", "accepted_at"):
            if key in task and not is_timestamp(task[key]):
                errors.append(f"{context}.{key} 存在时必须是带时区时间")
        dependencies = task.get("dependencies", [])
        if not isinstance(dependencies, list) or not all(isinstance(v, str) for v in dependencies):
            errors.append(f"{context}.dependencies 必须是字符串列表")
            dependencies = []
        verification = task.get("verification", [])
        if not isinstance(verification, list) or not all(nonempty_string(v) for v in verification):
            errors.append(f"{context}.verification 必须是字符串列表")
        for key in ("acceptance_evidence", "blocked_reason", "executor"):
            if key in task and not nonempty_string(task[key]):
                errors.append(f"{context}.{key} 必须是非空字符串")
        if status in ("working", "done", "accepted"):
            if not nonempty_string(task.get("executor")):
                errors.append(f"{context} {status} 必须有 executor")
            if not is_timestamp(task.get("started_at")):
                errors.append(f"{context} {status} 必须有 started_at")
        if status in ("done", "accepted"):
            if not is_timestamp(task.get("completed_at")):
                errors.append(f"{context} {status} 必须有 completed_at")
            if not string_list(verification, allow_empty=False):
                errors.append(f"{context} {status} 至少需要一个 verification")
        if status == "accepted":
            if not is_timestamp(task.get("accepted_at")):
                errors.append(f"{context} accepted 必须有 accepted_at")
            if not nonempty_string(task.get("acceptance_evidence")):
                errors.append(f"{context} accepted 必须有 acceptance_evidence")
        forbidden = {
            "todo": ("executor", "started_at", "completed_at", "accepted_at", "acceptance_evidence"),
            "working": ("completed_at", "accepted_at", "acceptance_evidence"),
            "done": ("accepted_at", "acceptance_evidence"),
        }
        forbidden_fields = forbidden.get(status, ()) if isinstance(status, str) else ()
        for key in forbidden_fields:
            if key in task:
                errors.append(f"{context} {status} 应省略 {key}")
        if status in ("todo", "working") and verification:
            errors.append(f"{context} {status} 应省略空 verification")
        if len(dependencies) != len(set(dependencies)):
            errors.append(f"{context}.dependencies 含重复 ID")
        if task_id in dependencies:
            errors.append(f"{context} 不能依赖自身")
    if len(ids) != len(set(ids)):
        errors.append("tasks 含重复 ID")
    known_ids = set(ids)
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            continue
        dependencies = task.get("dependencies", [])
        if not isinstance(dependencies, list) or not all(isinstance(value, str) for value in dependencies):
            continue
        for dependency in dependencies:
            if dependency not in known_ids:
                errors.append(f"tasks[{index}] 引用了不存在的依赖 {dependency!r}")
    if isinstance(next_number, int) and not isinstance(next_number, bool):
        used_numbers = [int(value.split("-")[1]) for value in ids]
        if used_numbers and next_number <= max(used_numbers):
            errors.append("next_task_number 必须大于所有已用编号")
    working_executors = [
        task.get("executor") for task in tasks
        if (
            isinstance(task, dict)
            and task.get("status") == "working"
            and isinstance(task.get("executor"), str)
        )
    ]
    if len(working_executors) != len(set(working_executors)):
        errors.append("同一执行者同一时间最多一个 working")
    graph = {
        task["id"]: task.get("dependencies", [])
        for task in tasks
        if (
            isinstance(task, dict)
            and isinstance(task.get("id"), str)
            and isinstance(task.get("dependencies", []), list)
            and all(isinstance(value, str) for value in task.get("dependencies", []))
        )
    }
    visiting = set()
    visited = set()

    def has_cycle(task_id: str) -> bool:
        if task_id in visiting:
            return True
        if task_id in visited:
            return False
        visiting.add(task_id)
        for dependency in graph.get(task_id, []):
            if has_cycle(dependency):
                return True
        visiting.remove(task_id)
        visited.add(task_id)
        return False

    if any(has_cycle(task_id) for task_id in graph):
        errors.append("dependency_cycle: 依赖关系存在循环")
    return errors


def validate_document(path: Path, data: Any, kind: str) -> list[str]:
    errors = validate_requirements_data(data) if kind == "requirements" else validate_todos_data(data)
    if kind == "requirements" and isinstance(data, dict) and isinstance(data.get("session"), dict):
        root_value = data["session"].get("project_root")
        if nonempty_string(root_value) and os.path.isabs(root_value):
            root = Path(root_value).resolve()
            resolved_file = path.resolve()
            if resolved_file == root or root in resolved_file.parents:
                errors.append("requirements.yml 不得位于项目目录内")
    return errors


def load_valid(path: Path, kind: str) -> Any:
    data = load_yaml(path)
    errors = validate_document(path, data, kind)
    if errors:
        raise StateError("; ".join(errors))
    return data


def mutate(path: Path, kind: str, operation: Callable[[Any], Any], arguments: Any) -> Any:
    def write() -> Any:
        data = load_valid(path, kind)
        result = operation(data)
        errors = validate_document(path, data, kind)
        if errors:
            raise StateError("; ".join(errors))
        atomic_write_yaml(path, data)
        return result

    return perform(path, kind, arguments, write)


def require_session(data: Any, session_id: str, project_root: Path) -> None:
    expected_root = str(project_root.resolve())
    if data["session"]["id"] != session_id:
        raise StateError("会话 ID 与 requirements.yml 不匹配")
    if data["session"]["project_root"] != expected_root:
        raise StateError("项目根目录与 requirements.yml 不匹配")


def find_item(items: list[dict[str, Any]], item_id: str, label: str) -> dict[str, Any]:
    for item in items:
        if item.get("id") == item_id:
            return item
    raise StateError(f"找不到{label}: {item_id}")


def initialize_requirements(arguments: argparse.Namespace) -> dict[str, Any]:
    path = arguments.file.resolve()
    root = arguments.project_root.resolve()
    if path == root or root in path.parents:
        raise StateError("requirements.yml 不得位于项目目录内")

    def write() -> dict[str, Any]:
        if path.exists():
            raise StateError(f"文件已存在: {path}")
        now = utc_now()
        data = {
            "schema_version": "1.0",
            "session": {
                "id": arguments.session_id,
                "project_root": str(root),
                "created_at": now,
                "updated_at": now,
            },
            "next_requirement_number": 1,
            "requirements": [],
            "response_review": {"results": []},
        }
        errors = validate_document(path, data, "requirements")
        if errors:
            raise StateError("; ".join(errors))
        atomic_write_yaml(path, data)
        return {"file": str(path)}

    return perform(path, "requirements", arguments, write)


def initialize_todos(arguments: argparse.Namespace) -> dict[str, Any]:
    path = arguments.file

    def write() -> dict[str, Any]:
        if path.exists():
            raise StateError(f"文件已存在: {path}")
        data = {
            "schema_version": "1.0",
            "updated_at": utc_now(),
            "next_task_number": 1,
            "tasks": [],
        }
        atomic_write_yaml(path, data)
        return {"file": str(path.resolve())}

    return perform(path, "todos", arguments, write)


def upsert_requirement(arguments: argparse.Namespace) -> dict[str, Any]:
    def operation(data: Any) -> Any:
        require_session(data, arguments.session_id, arguments.project_root)
        if arguments.id is None:
            if not nonempty_string(arguments.text):
                raise StateError("新增要求必须提供 --text")
            number = data["next_requirement_number"]
            requirement_id = f"REQ-{number:03d}"
            data["next_requirement_number"] = number + 1
            requirement = {"id": requirement_id, "text": arguments.text}
            scope = arguments.scope or "session"
            polarity = arguments.polarity or "required"
            if polarity != "required":
                requirement["polarity"] = polarity
            if scope != "session":
                requirement["scope"] = scope
            if arguments.task_id:
                requirement["task_ids"] = arguments.task_id
            if arguments.source_quote and arguments.source_quote != arguments.text:
                requirement["source_quote"] = arguments.source_quote
            if scope != "session" and arguments.expires_when:
                requirement["expires_when"] = arguments.expires_when
            data["requirements"].append(requirement)
        else:
            requirement = find_item(data["requirements"], arguments.id, "要求")
            requirement_id = arguments.id
            if arguments.text is not None:
                requirement["text"] = arguments.text
            if arguments.polarity is not None:
                if arguments.polarity == "required":
                    requirement.pop("polarity", None)
                else:
                    requirement["polarity"] = arguments.polarity
            if arguments.source_quote is not None:
                if arguments.source_quote == requirement["text"]:
                    requirement.pop("source_quote", None)
                else:
                    requirement["source_quote"] = arguments.source_quote
            if arguments.scope is not None:
                if arguments.scope == "session":
                    requirement.pop("scope", None)
                else:
                    requirement["scope"] = arguments.scope
            if arguments.task_id is not None:
                if arguments.task_id:
                    requirement["task_ids"] = arguments.task_id
                else:
                    requirement.pop("task_ids", None)
            if requirement.get("scope", "session") == "session":
                requirement.pop("task_ids", None)
                requirement.pop("expires_when", None)
            elif arguments.expires_when is not None:
                requirement["expires_when"] = arguments.expires_when
        data["session"]["updated_at"] = utc_now()
        data["response_review"] = {"results": []}
        return {"id": requirement_id}

    return mutate(arguments.file, "requirements", operation, arguments)


def remove_requirement(arguments: argparse.Namespace) -> dict[str, Any]:
    def operation(data: Any) -> Any:
        require_session(data, arguments.session_id, arguments.project_root)
        find_item(data["requirements"], arguments.id, "要求")
        data["requirements"] = [item for item in data["requirements"] if item["id"] != arguments.id]
        data["session"]["updated_at"] = utc_now()
        data["response_review"] = {"results": []}
        return {"removed": arguments.id}

    return mutate(arguments.file, "requirements", operation, arguments)


def review_requirements(arguments: argparse.Namespace) -> dict[str, Any]:
    try:
        results = json.loads(arguments.results)
    except json.JSONDecodeError as exc:
        raise StateError(f"--results 不是合法 JSON: {exc}") from exc
    if not isinstance(results, list):
        raise StateError("--results 必须是 JSON 数组")
    results = [
        {key: value for key, value in result.items() if key != "reason" or value is not None}
        if isinstance(result, dict) else result
        for result in results
    ]

    def operation(data: Any) -> Any:
        require_session(data, arguments.session_id, arguments.project_root)
        data["response_review"] = {
            "reviewed_at": utc_now(),
            "results": sorted(results, key=lambda item: item.get("requirement_id", "") if isinstance(item, dict) else ""),
        }
        data["session"]["updated_at"] = utc_now()
        return {"reviewed": len(results)}

    return mutate(arguments.file, "requirements", operation, arguments)


def add_todo(arguments: argparse.Namespace) -> dict[str, Any]:
    def operation(data: Any) -> Any:
        number = data["next_task_number"]
        task_id = f"TASK-{number:03d}"
        now = utc_now()
        data["next_task_number"] = number + 1
        task = {
            "id": task_id,
            "title": arguments.title,
            "acceptance_criteria": arguments.acceptance_criteria,
            "status": "todo",
            "created_at": now,
            "updated_at": now,
        }
        if arguments.dependency:
            task["dependencies"] = arguments.dependency
        data["tasks"].append(task)
        data["updated_at"] = now
        return {"id": task_id}

    return mutate(arguments.file, "todos", operation, arguments)


def remove_todo(arguments: argparse.Namespace) -> dict[str, Any]:
    def operation(data: Any) -> Any:
        find_item(data["tasks"], arguments.id, "任务")
        dependents = [
            item["id"] for item in data["tasks"]
            if arguments.id in item.get("dependencies", [])
        ]
        if dependents:
            raise StateError(f"dependency_in_use: 仍被 {', '.join(dependents)} 依赖")
        data["tasks"] = [item for item in data["tasks"] if item["id"] != arguments.id]
        data["updated_at"] = utc_now()
        return {"removed": arguments.id}

    return mutate(arguments.file, "todos", operation, arguments)


def transition_todo(arguments: argparse.Namespace) -> dict[str, Any]:
    expected = {"working": "todo", "done": "working", "accepted": "done"}

    def operation(data: Any) -> Any:
        task = find_item(data["tasks"], arguments.id, "任务")
        if task["status"] != expected[arguments.to]:
            raise StateError(f"非法状态迁移: {task['status']} → {arguments.to}")
        now = utc_now()
        if arguments.to == "working":
            if not nonempty_string(arguments.executor):
                raise StateError("转为 working 必须提供 --executor")
            status_by_id = {item["id"]: item["status"] for item in data["tasks"]}
            incomplete = [
                dependency for dependency in task.get("dependencies", [])
                if status_by_id.get(dependency) not in ("done", "accepted")
            ]
            if incomplete:
                raise StateError(
                    f"dependency_not_complete: {', '.join(incomplete)} 尚未完成或验收"
                )
            for other in data["tasks"]:
                if (
                    other["id"] != task["id"]
                    and other["status"] == "working"
                    and other.get("executor") == arguments.executor
                ):
                    raise StateError(f"执行者已有 working 任务: {other['id']}")
            task["executor"] = arguments.executor
            task["started_at"] = now
        elif arguments.to == "done":
            if not arguments.verification:
                raise StateError("转为 done 至少需要一个 --verification")
            task["verification"] = arguments.verification
            task["completed_at"] = now
        else:
            if not nonempty_string(arguments.acceptance_evidence):
                raise StateError("转为 accepted 必须提供 --acceptance-evidence")
            task["acceptance_evidence"] = arguments.acceptance_evidence
            task["accepted_at"] = now
        task["status"] = arguments.to
        task["updated_at"] = now
        data["updated_at"] = now
        return {"id": task["id"], "status": task["status"]}

    return mutate(arguments.file, "todos", operation, arguments)


def set_blocked(arguments: argparse.Namespace, clear: bool = False) -> dict[str, Any]:
    def operation(data: Any) -> Any:
        task = find_item(data["tasks"], arguments.id, "任务")
        if not clear and task["status"] not in ("todo", "working"):
            raise StateError("只有 todo 或 working 任务可以设置阻塞")
        if clear:
            task.pop("blocked_reason", None)
        else:
            task["blocked_reason"] = arguments.reason
        task["updated_at"] = utc_now()
        data["updated_at"] = task["updated_at"]
        return {"id": task["id"], "blocked_reason": task.get("blocked_reason")}

    return mutate(arguments.file, "todos", operation, arguments)


def clean_accepted(arguments: argparse.Namespace) -> dict[str, Any]:
    def operation(data: Any) -> Any:
        accepted_ids = [task["id"] for task in data["tasks"] if task["status"] == "accepted"]
        data["tasks"] = [task for task in data["tasks"] if task["status"] != "accepted"]
        for task in data["tasks"]:
            if "dependencies" in task:
                task["dependencies"] = [value for value in task["dependencies"] if value not in accepted_ids]
                if not task["dependencies"]:
                    task.pop("dependencies")
        data["updated_at"] = utc_now()
        return {"removed": accepted_ids}

    return mutate(arguments.file, "todos", operation, arguments)


def validate_command(arguments: argparse.Namespace, kind: str) -> dict[str, Any]:
    load_valid(arguments.file, kind)
    return {"valid": True, "file": str(arguments.file.resolve())}


def add_session_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--project-root", required=True, type=Path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    groups = parser.add_subparsers(dest="kind", required=True)
    requirements = groups.add_parser("requirements")
    requirement_commands = requirements.add_subparsers(dest="command", required=True)
    init = requirement_commands.add_parser("init")
    add_session_arguments(init)
    init.set_defaults(handler=initialize_requirements)
    upsert = requirement_commands.add_parser("upsert")
    add_session_arguments(upsert)
    upsert.add_argument("--id")
    upsert.add_argument("--text")
    upsert.add_argument("--polarity", choices=("required", "forbidden"))
    upsert.add_argument("--scope", choices=sorted(SCOPE_TYPES))
    upsert.add_argument("--source-quote")
    upsert.add_argument("--task-id", action="append")
    upsert.add_argument("--expires-when")
    upsert.set_defaults(handler=upsert_requirement)
    remove = requirement_commands.add_parser("remove")
    add_session_arguments(remove)
    remove.add_argument("--id", required=True)
    remove.set_defaults(handler=remove_requirement)
    review = requirement_commands.add_parser("review")
    add_session_arguments(review)
    review.add_argument("--results", required=True)
    review.set_defaults(handler=review_requirements)
    validate = requirement_commands.add_parser("validate")
    validate.add_argument("--file", required=True, type=Path)
    validate.set_defaults(handler=lambda args: validate_command(args, "requirements"))

    todos = groups.add_parser("todos")
    todo_commands = todos.add_subparsers(dest="command", required=True)
    init_todos = todo_commands.add_parser("init")
    init_todos.add_argument("--file", required=True, type=Path)
    init_todos.set_defaults(handler=initialize_todos)
    add = todo_commands.add_parser("add")
    add.add_argument("--file", required=True, type=Path)
    add.add_argument("--title", required=True)
    add.add_argument("--acceptance-criteria", action="append", required=True)
    add.add_argument("--dependency", action="append")
    add.set_defaults(handler=add_todo)
    remove_todo_parser = todo_commands.add_parser("remove")
    remove_todo_parser.add_argument("--file", required=True, type=Path)
    remove_todo_parser.add_argument("--id", required=True)
    remove_todo_parser.set_defaults(handler=remove_todo)
    transition = todo_commands.add_parser("transition")
    transition.add_argument("--file", required=True, type=Path)
    transition.add_argument("--id", required=True)
    transition.add_argument("--to", required=True, choices=("working", "done", "accepted"))
    transition.add_argument("--executor")
    transition.add_argument("--verification", action="append")
    transition.add_argument("--acceptance-evidence")
    transition.set_defaults(handler=transition_todo)
    blocked = todo_commands.add_parser("set-blocked")
    blocked.add_argument("--file", required=True, type=Path)
    blocked.add_argument("--id", required=True)
    blocked.add_argument("--reason", required=True)
    blocked.set_defaults(handler=set_blocked)
    clear = todo_commands.add_parser("clear-blocked")
    clear.add_argument("--file", required=True, type=Path)
    clear.add_argument("--id", required=True)
    clear.set_defaults(handler=lambda args: set_blocked(args, clear=True))
    clean = todo_commands.add_parser("clean-accepted")
    clean.add_argument("--file", required=True, type=Path)
    clean.set_defaults(handler=clean_accepted)
    validate_todos = todo_commands.add_parser("validate")
    validate_todos.add_argument("--file", required=True, type=Path)
    validate_todos.set_defaults(handler=lambda args: validate_command(args, "todos"))
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    try:
        result = arguments.handler(arguments)
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return 0
    except StateError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    except (YamlError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
