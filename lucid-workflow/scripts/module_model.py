#!/usr/bin/env python3
"""Shared helpers for optional functional-module metadata."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from yaml_common import nonempty_string, normalize_project_path


DEPENDENCY_KINDS = {"call", "config", "data", "event"}


def resolve_target(source_directory: str, target: str) -> str:
    target = normalize_project_path(target)
    if "/" in target or source_directory == ".":
        return target
    return normalize_project_path(f"{source_directory}/{target}")


def module_dependencies(data: dict[str, Any], source_directory: str) -> list[dict[str, str]]:
    dependencies = data.get("depends_on", [])
    if not isinstance(dependencies, list):
        return []
    result = []
    for dependency in dependencies:
        if not isinstance(dependency, dict) or not isinstance(dependency.get("target"), str):
            continue
        result.append({
            "target": resolve_target(source_directory, dependency["target"]),
            "kind": dependency.get("kind", "call"),
            "reason": dependency.get("reason", ""),
        })
    return result


def module_text(data: dict[str, Any], source_directory: str) -> str:
    parts = []
    for key in ("boundary", "entry"):
        value = data.get(key)
        if isinstance(value, str):
            parts.append(value)
    for dependency in module_dependencies(data, source_directory):
        parts.extend(dependency.values())
    return " ".join(parts).casefold()


def validate_module_fields(
    data: Any,
    source_directory: str,
    root: Path,
    errors: list[str],
    context: str,
) -> None:
    if not isinstance(data, dict):
        return
    for key in ("boundary", "entry"):
        if key in data and not nonempty_string(data[key]):
            errors.append(f"{context}.{key} 必须是非空字符串")
    dependencies = data.get("depends_on", [])
    if not isinstance(dependencies, list):
        errors.append(f"{context}.depends_on 必须是列表")
        return
    seen = set()
    for index, dependency in enumerate(dependencies):
        item_context = f"{context}.depends_on[{index}]"
        if not isinstance(dependency, dict):
            errors.append(f"{item_context} 必须是映射")
            continue
        unknown = set(dependency) - {"target", "kind", "reason"}
        if unknown:
            errors.append(f"{item_context} 含未知字段 {sorted(unknown)}")
        target = dependency.get("target")
        reason = dependency.get("reason")
        kind = dependency.get("kind", "call")
        if not nonempty_string(target):
            errors.append(f"{item_context}.target 必须是非空字符串")
            continue
        if not nonempty_string(reason):
            errors.append(f"{item_context}.reason 必须是非空字符串")
        if not isinstance(kind, str) or kind not in DEPENDENCY_KINDS:
            errors.append(f"{item_context}.kind 无效: {kind!r}")
            continue
        try:
            resolved = resolve_target(source_directory, target)
        except ValueError as exc:
            errors.append(f"{item_context}.target {exc}")
            continue
        identity = (resolved, kind)
        if identity in seen:
            errors.append(f"{item_context} 重复依赖 {resolved!r} ({kind})")
        seen.add(identity)
        candidate = root / resolved
        if not candidate.exists():
            errors.append(f"{item_context}.target 不存在: {resolved!r}")
