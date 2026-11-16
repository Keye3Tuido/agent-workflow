#!/usr/bin/env python3
"""Read-only structural checker for a project's .doc-forest."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from yaml_common import YamlError, load_yaml, nonempty_string, normalize_project_path, require_keys
from module_model import validate_module_fields


class ConfigError(ValueError):
    pass


def posix_relative(path: Path, root: Path) -> str:
    relative = path.relative_to(root).as_posix()
    return "." if relative == "." else relative


def validate_config(data: Any) -> dict[str, Any]:
    errors = require_keys(data, {"schema_version", "write_check", "forest"}, context="配置")
    if errors:
        raise ConfigError("; ".join(errors))
    if data.get("schema_version") != "1.0":
        errors.append("schema_version 必须是字符串 '1.0'")
    errors.extend(require_keys(data.get("write_check"), {"excluded_paths"}, context="write_check"))
    errors.extend(
        require_keys(data.get("forest"), {"allowlist", "excluded_paths"}, context="forest")
    )
    for section, key in (
        ("write_check", "excluded_paths"),
        ("forest", "allowlist"),
        ("forest", "excluded_paths"),
    ):
        section_data = data.get(section)
        values = section_data.get(key) if isinstance(section_data, dict) else None
        if not isinstance(values, list):
            errors.append(f"{section}.{key} 必须是列表")
            continue
        seen = set()
        for value in values:
            try:
                normalized = normalize_project_path(value)
                if normalized in seen:
                    errors.append(f"{section}.{key} 含重复路径 {normalized!r}")
                seen.add(normalized)
            except ValueError as exc:
                errors.append(str(exc))
    if errors:
        raise ConfigError("; ".join(errors))
    data["write_check"]["excluded_paths"] = [
        normalize_project_path(value) for value in data["write_check"]["excluded_paths"]
    ]
    data["forest"]["allowlist"] = [
        normalize_project_path(value) for value in data["forest"]["allowlist"]
    ]
    data["forest"]["excluded_paths"] = [
        normalize_project_path(value) for value in data["forest"]["excluded_paths"]
    ]
    return data


class ForestChecker:
    def __init__(self, root: Path, requested_paths: list[str] | None = None):
        self.root = root.resolve()
        self.forest_root = self.root / ".doc-forest"
        self.findings: list[dict[str, str]] = []
        self.info_cache: dict[str, Any] = {}
        self.info_errors: dict[str, str] = {}
        self.expected: set[str] = set()
        self.pruned: set[str] = set()
        self.checked: set[str] = set()
        self.requested_paths = requested_paths or []
        config_path = self.root / ".doc-forest.yml"
        if not config_path.is_file():
            raise ConfigError(f"缺少配置文件: {config_path}")
        try:
            self.config = validate_config(load_yaml(config_path))
        except YamlError as exc:
            raise ConfigError(str(exc)) from exc
        self.allowlist = self.config["forest"]["allowlist"]
        self.excluded = self.config["forest"]["excluded_paths"]

    def add(self, code: str, path: str, message: str) -> None:
        self.findings.append({"code": code, "path": path, "message": message})

    def source_path(self, relative: str) -> Path:
        return self.root if relative == "." else self.root / relative

    def mirror_path(self, relative: str) -> Path:
        return self.forest_root if relative == "." else self.forest_root / relative

    def is_excluded(self, relative: str) -> bool:
        if relative == ".doc-forest" or relative.startswith(".doc-forest/"):
            return True
        if relative == ".cache" or relative.startswith(".cache/"):
            return True
        for excluded in self.excluded:
            if "/" not in excluded:
                if excluded in PurePosixPath(relative).parts:
                    return True
            elif relative == excluded or relative.startswith(f"{excluded}/"):
                return True
        return False

    def is_allowed(self, relative: str) -> bool:
        if not self.allowlist or relative == ".":
            return True
        for allowed in self.allowlist:
            if relative == allowed or relative.startswith(f"{allowed}/"):
                return True
            if allowed.startswith(f"{relative}/"):
                return True
        return False

    def included(self, relative: str) -> bool:
        return not self.is_excluded(relative) and self.is_allowed(relative)

    def source_directories(self, relative: str) -> list[str]:
        directory = self.source_path(relative)
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            self.add("source_read_error", relative, str(exc))
            return []
        names = []
        for entry in entries:
            if entry.name == ".doc-forest" and relative == ".":
                continue
            try:
                is_directory = entry.is_dir(follow_symlinks=False)
            except OSError:
                is_directory = False
            if not is_directory:
                continue
            child = entry.name if relative == "." else f"{relative}/{entry.name}"
            if self.included(child):
                names.append(entry.name)
        return sorted(names)

    def load_info(self, relative: str) -> Any:
        if relative in self.info_cache or relative in self.info_errors:
            return self.info_cache.get(relative)
        path = self.mirror_path(relative) / "info.yml"
        if not path.is_file():
            return None
        try:
            data = load_yaml(path)
            self.info_cache[relative] = data
            return data
        except YamlError as exc:
            self.info_errors[relative] = str(exc)
            return None

    def raw_child_rules(self, relative: str) -> dict[str, bool]:
        data = self.load_info(relative)
        if not isinstance(data, dict) or not isinstance(data.get("directories"), list):
            return {}
        rules = {}
        for entry in data["directories"]:
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                recurse = entry.get("recurse")
                if isinstance(recurse, bool):
                    rules[entry["name"]] = recurse
        return rules

    def enumerate_expected(self) -> None:
        def visit(relative: str) -> None:
            self.expected.add(relative)
            rules = self.raw_child_rules(relative)
            for name in self.source_directories(relative):
                child = name if relative == "." else f"{relative}/{name}"
                if rules.get(name, True):
                    visit(child)
                else:
                    self.expected.add(child)
                    self.pruned.add(child)
                    self.load_info(child)

        visit(".")

    def determine_scope(self) -> None:
        if not self.requested_paths:
            self.checked = set(self.expected)
            return
        for requested in self.requested_paths:
            try:
                normalized = normalize_project_path(requested, allow_root=True)
            except ValueError as exc:
                raise ConfigError(str(exc)) from exc
            absolute = self.source_path(normalized)
            if not absolute.exists():
                raise ConfigError(f"局部检查路径不存在: {normalized}")
            if not absolute.is_dir():
                normalized = PurePosixPath(normalized).parent.as_posix()
            if normalized not in self.expected:
                continue
            current = normalized
            while True:
                self.checked.add(current)
                if current == ".":
                    break
                current = PurePosixPath(current).parent.as_posix()

    def validate_info(self, relative: str) -> None:
        mirror = self.mirror_path(relative)
        info_path = mirror / "info.yml"
        display_path = posix_relative(info_path, self.root)
        if relative in self.info_errors:
            self.add("invalid_yaml", display_path, self.info_errors[relative])
            return
        if not info_path.is_file():
            self.add("missing_info", display_path, "缺少 info.yml")
            return
        data = self.info_cache.get(relative)
        errors = require_keys(
            data,
            {"schema_version", "purpose", "contents_summary"},
            {"files", "directories", "boundary", "entry", "depends_on"},
            "info.yml",
        )
        if not isinstance(data, dict):
            for message in errors:
                self.add("invalid_info", display_path, message)
            return
        if data.get("schema_version") != "1.0":
            errors.append("schema_version 必须是字符串 '1.0'")
        for key in ("purpose", "contents_summary"):
            if not nonempty_string(data.get(key)):
                errors.append(f"{key} 必须是非空字符串")
        validate_module_fields(data, relative, self.root, errors, "info.yml")
        files = data.get("files", [])
        directories = data.get("directories", [])
        if not isinstance(files, list):
            errors.append("files 必须是列表")
            files = []
        if not isinstance(directories, list):
            errors.append("directories 必须是列表")
            directories = []
        source = self.source_path(relative)
        actual_directories = set(self.source_directories(relative))
        if relative in self.pruned:
            actual_directories = set()
        self.validate_file_entries(files, source, errors)
        directory_names = self.validate_directory_entries(directories, errors)
        for name in sorted(actual_directories - directory_names):
            errors.append(f"缺少直接子目录条目 {name!r}")
        for name in sorted(directory_names - actual_directories):
            errors.append(f"目录条目不存在或已被排除: {name!r}")
        for message in errors:
            self.add("invalid_info", display_path, message)

    def validate_file_entries(self, entries: list[Any], source: Path, errors: list[str]) -> set[str]:
        names: list[str] = []
        relative = posix_relative(source, self.root)
        for index, entry in enumerate(entries):
            errors.extend(
                require_keys(
                    entry,
                    {"name", "purpose"},
                    {"details", "boundary", "entry", "depends_on", "core"},
                    f"files[{index}]",
                )
            )
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not self.valid_name(name):
                errors.append(f"files[{index}].name 无效: {name!r}")
                continue
            names.append(name)
            if not nonempty_string(entry.get("purpose")):
                errors.append(f"files[{index}].purpose 必须是非空字符串")
            if "details" in entry and not isinstance(entry["details"], str):
                errors.append(f"files[{index}].details 必须是字符串")
            if "core" in entry and entry["core"] is not True:
                errors.append(f"files[{index}].core 仅在值为 true 时写出")
            candidate = source / name
            if not os.path.lexists(candidate) or candidate.is_dir():
                errors.append(f"文件条目不存在或不是直接文件: {name!r}")
            validate_module_fields(entry, relative, self.root, errors, f"files[{index}]")
        self.validate_name_order(names, "files", errors)
        return set(names)

    def validate_directory_entries(self, entries: list[Any], errors: list[str]) -> set[str]:
        names: list[str] = []
        for index, entry in enumerate(entries):
            errors.extend(
                require_keys(entry, {"name"}, {"recurse"}, f"directories[{index}]")
            )
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not self.valid_name(name):
                errors.append(f"directories[{index}].name 无效: {name!r}")
                continue
            names.append(name)
            if "recurse" in entry and not isinstance(entry["recurse"], bool):
                errors.append(f"directories[{index}].recurse 必须是布尔值")
            if entry.get("recurse") is True:
                errors.append(f"directories[{index}] 默认 recurse: true，应省略")
        self.validate_name_order(names, "directories", errors)
        return set(names)

    @staticmethod
    def valid_name(name: Any) -> bool:
        return (
            nonempty_string(name)
            and name not in (".", "..")
            and "/" not in name
            and "\\" not in name
        )

    @staticmethod
    def validate_name_order(names: list[str], context: str, errors: list[str]) -> None:
        if len(names) != len(set(names)):
            errors.append(f"{context} 含重复名称")
        if names != sorted(names):
            errors.append(f"{context} 必须按 name 排序")

    def check_mirror_files(self) -> None:
        if not self.forest_root.is_dir():
            return
        for current, directory_names, file_names in os.walk(self.forest_root, followlinks=False):
            current_path = Path(current)
            relative = posix_relative(current_path, self.forest_root)
            if self.requested_paths and relative not in self.checked:
                directory_names[:] = []
                continue
            directory_names[:] = sorted(
                name
                for name in directory_names
                if not (current_path / name).is_symlink()
            )
            for name in sorted(file_names):
                if name != "info.yml":
                    path = current_path / name
                    display = posix_relative(path, self.root)
                    self.add("unexpected_file", display, "森林中只允许 info.yml")

    def run(self) -> dict[str, Any]:
        self.enumerate_expected()
        self.determine_scope()
        for relative in sorted(self.checked, key=lambda value: (value.count("/"), value)):
            mirror = self.mirror_path(relative)
            if not mirror.is_dir():
                self.add(
                    "missing_directory",
                    posix_relative(mirror, self.root),
                    "缺少镜像目录",
                )
            self.load_info(relative)
            self.validate_info(relative)
        if not self.requested_paths:
            for current, directory_names, _ in os.walk(self.forest_root, followlinks=False):
                current_path = Path(current)
                relative = posix_relative(current_path, self.forest_root)
                if relative != "." and relative not in self.expected:
                    self.add(
                        "unexpected_directory",
                        posix_relative(current_path, self.root),
                        "没有对应源目录或已停止递归",
                    )
                directory_names[:] = [
                    name for name in directory_names if not (current_path / name).is_symlink()
                ]
        self.check_mirror_files()
        self.findings.sort(key=lambda item: (item["path"], item["code"], item["message"]))
        return {
            "schema_version": "1.0",
            "root": str(self.root),
            "scope": "partial" if self.requested_paths else "full",
            "valid": not self.findings,
            "checked_directories": sorted(self.checked),
            "findings": self.findings,
            "summary": {"error_count": len(self.findings)},
        }


def check_forest(root: Path, requested_paths: list[str] | None = None) -> dict[str, Any]:
    return ForestChecker(root, requested_paths).run()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="项目根目录")
    parser.add_argument("--path", action="append", default=[], help="局部检查的相对文件或目录")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    arguments = parser.parse_args()
    try:
        report = check_forest(arguments.root, arguments.path)
    except (ConfigError, OSError) as exc:
        if arguments.format == "json":
            print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if arguments.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for finding in report["findings"]:
            print(f"ERROR {finding['code']} {finding['path']}: {finding['message']}")
        print(
            f"Checked {len(report['checked_directories'])} directories; "
            f"found {len(report['findings'])} errors."
        )
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
