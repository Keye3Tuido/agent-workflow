#!/usr/bin/env python3
"""Append-only JSONL operation audit for shared state files."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Callable

from yaml_common import file_lock, load_yaml, utc_now


def audit_path(state_path: Path, kind: str) -> Path:
    if kind == "requirements":
        return state_path.with_name("requirements-audit.jsonl")
    return state_path.with_name(".agent-workflow-audit.jsonl")


def append_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as target:
        target.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        target.flush()
        os.fsync(target.fileno())


def metadata(path: Path, kind: str, arguments: Any) -> dict[str, Any]:
    actor = None
    session_id = None
    if kind == "requirements":
        actor = getattr(arguments, "session_id", None)
        session_id = actor
    else:
        actor = getattr(arguments, "executor", None) or os.environ.get("AGENT_WORKFLOW_SESSION_ID")
        session_id = os.environ.get("AGENT_WORKFLOW_SESSION_ID") or actor
    result = {
        "operation_id": uuid.uuid4().hex,
        "time": utc_now(),
        "kind": kind,
        "operation": getattr(arguments, "command", "write"),
        "actor": actor or "unknown",
        "session_id": session_id or "unknown",
    }
    target_id = getattr(arguments, "id", None)
    if target_id:
        result["target_id"] = target_id
    to = getattr(arguments, "to", None)
    if to:
        result["to"] = to
    if kind == "todos" and target_id and path.is_file():
        try:
            data = load_yaml(path)
            task = next(
                (item for item in data.get("tasks", []) if item.get("id") == target_id),
                None,
            )
            if task:
                result["from"] = task.get("status")
        except Exception:
            pass
    return result


def perform(
    path: Path,
    kind: str,
    arguments: Any,
    write: Callable[[], Any],
) -> Any:
    """Run a state write and its audit events under the same stable file lock."""
    log_path = audit_path(path, kind)
    with file_lock(path):
        operation = metadata(path, kind, arguments)
        append_event(log_path, {"event": "prepared", **operation})
        try:
            result = write()
        except BaseException as exc:
            if isinstance(exc, OSError):
                error = "文件系统操作失败"
            elif isinstance(exc, ValueError):
                error = "状态或数据校验失败"
            else:
                error = "状态写入失败"
            failure = {
                "event": "failed",
                **operation,
                "error_type": type(exc).__name__,
                "error": error,
            }
            try:
                append_event(log_path, failure)
            except OSError:
                pass
            raise
        completed = {
            "event": "committed",
            **operation,
            "time": utc_now(),
            "state_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        if isinstance(result, dict) and result.get("id"):
            completed["target_id"] = result["id"]
        append_event(log_path, completed)
        return result
