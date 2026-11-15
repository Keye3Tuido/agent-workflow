#!/usr/bin/env python3
"""Show one functional module, its direct dependencies, and direct dependents."""

from __future__ import annotations

import argparse
import json
import sys
import sqlite3
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any

from check_doc_forest import ConfigError, ForestChecker, normalize_project_path
from context_cache import ContextCache
from module_model import module_dependencies


def prepare(root: Path, use_cache: bool = True, cache_path: Path | None = None):
    cache_path = cache_path or root / ".cache" / "agent-workflow" / "context.sqlite"
    if use_cache and cache_path.is_file():
        try:
            info_cache = ContextCache.load_info(cache_path)
            return SimpleNamespace(
                root=root.resolve(),
                expected=set(info_cache),
                info_cache=info_cache,
                source_path=lambda relative: root.resolve() if relative == "." else root.resolve() / relative,
            )
        except (sqlite3.Error, json.JSONDecodeError):
            pass
    checker = ForestChecker(root)
    checker.enumerate_expected()
    for relative in checker.expected:
        checker.load_info(relative)
    return checker


def dependency_owners(checker: ForestChecker):
    for relative in sorted(checker.expected):
        data = checker.info_cache.get(relative)
        if not isinstance(data, dict):
            continue
        yield relative, data
        for entry in data.get("files", []):
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                source = entry["name"] if relative == "." else f"{relative}/{entry['name']}"
                yield source, entry


def dependents(checker: ForestChecker, target: str) -> list[dict[str, str]]:
    result = []
    for owner_source, data in dependency_owners(checker):
        source_directory = PurePosixPath(owner_source).parent.as_posix()
        for dependency in module_dependencies(data, source_directory):
            if dependency["target"] == target:
                result.append({"source": owner_source, **dependency})
    return result


def build_architecture(
    root: Path,
    module_path: str,
    use_cache: bool = True,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    checker = prepare(root, use_cache=use_cache, cache_path=cache_path)
    normalized = normalize_project_path(module_path, allow_root=True)
    source = checker.source_path(normalized)
    if not source.exists():
        raise ConfigError(f"模块路径不存在: {normalized}")
    if source.is_dir():
        data = checker.info_cache.get(normalized)
        if not isinstance(data, dict):
            raise ConfigError(f"目录模块缺少有效 info.yml: {normalized}")
        source_directory = normalized
        module = {
            "path": normalized,
            "kind": "directory",
            "purpose": data["purpose"],
            "contents_summary": data["contents_summary"],
        }
        for key in ("boundary", "entry"):
            if key in data:
                module[key] = data[key]
        members = []
        for entry in data.get("files", []):
            path = entry["name"] if normalized == "." else f"{normalized}/{entry['name']}"
            members.append({"path": path, "purpose": entry["purpose"]})
        for entry in data.get("directories", []):
            path = entry["name"] if normalized == "." else f"{normalized}/{entry['name']}"
            members.append({"path": path, "kind": "directory"})
        module["members"] = members
    else:
        source_directory = PurePosixPath(normalized).parent.as_posix()
        data = checker.info_cache.get(source_directory)
        if not isinstance(data, dict):
            raise ConfigError(f"文件模块所属目录缺少有效 info.yml: {source_directory}")
        entry = next(
            (
                item for item in data.get("files", [])
                if isinstance(item, dict) and item.get("name") == source.name
            ),
            None,
        )
        if entry is None:
            raise ConfigError(f"文件模块未在所属 info.yml 登记: {normalized}")
        module = {
            "path": normalized,
            "kind": "file",
            "purpose": entry["purpose"],
        }
        for key in ("details", "boundary", "entry"):
            if key in entry:
                module[key] = entry[key]
        data = entry
    dependencies = module_dependencies(data, source_directory)
    return {
        "schema_version": "1.0",
        "module": module,
        "depends_on": dependencies,
        "dependents": dependents(checker, normalized),
        "limitations": ["仅展示声明的直接关系，不证明完整运行时调用链"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--path", required=True, help="目录或文件模块的项目相对路径")
    parser.add_argument("--cache", type=Path, help="复用派生 SQLite 缓存")
    parser.add_argument("--no-cache", action="store_true", help="直接读取森林文件")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    arguments = parser.parse_args()
    try:
        report = build_architecture(
            arguments.root,
            arguments.path,
            use_cache=not arguments.no_cache,
            cache_path=arguments.cache,
        )
    except (ConfigError, OSError, KeyError) as exc:
        if arguments.format == "json":
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if arguments.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    module = report["module"]
    print(f"MODULE: {module['path']} ({module['kind']})")
    print(f"PURPOSE: {module['purpose']}")
    if "boundary" in module:
        print(f"BOUNDARY: {module['boundary']}")
    if "entry" in module:
        print(f"ENTRY: {module['entry']}")
    for member in module.get("members", []):
        print(f"MEMBER: {member['path']}  {member.get('purpose', '')}".rstrip())
    for dependency in report["depends_on"]:
        print(f"DEPENDS_ON: {dependency['target']}  [{dependency['kind']}] {dependency['reason']}")
    for dependency in report["dependents"]:
        print(f"DEPENDENT: {dependency['source']}  [{dependency['kind']}] {dependency['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
