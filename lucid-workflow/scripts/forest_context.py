#!/usr/bin/env python3
"""Locate task-relevant modules, source files, tests, and line numbers."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

from check_doc_forest import ConfigError, ForestChecker, normalize_project_path
from context_cache import ContextCache
from module_model import module_dependencies, module_text


STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "project", "task", "file", "files",
    "directory", "when", "what", "how", "from", "into", "should", "need", "needs", "use",
    "当前", "项目", "任务", "要求", "相关", "文件", "目录", "功能", "实现", "如何", "是否",
    "避免", "需要", "进行", "使用", "一个", "工作", "信息", "描述",
}
SPLIT_CHINESE = re.compile(
    r"(?:的|和|与|或|及|并|根据|需要|当前|项目|要求|任务|实现|功能|文件|目录|检查|修改|精简|避免|如何|是否|支持|相关|进行|使用)"
)


def derive_keywords(task: str, explicit: list[str]) -> list[str]:
    keywords = []
    for value in explicit:
        value = value.strip().casefold()
        if value and value not in keywords:
            keywords.append(value)
    if keywords:
        return keywords[:20]
    candidates = re.findall(r"[A-Za-z_][A-Za-z0-9_.-]{1,}|[\u4e00-\u9fff]{2,}", task)
    for candidate in candidates:
        parts = [candidate]
        if re.fullmatch(r"[\u4e00-\u9fff]+", candidate) and len(candidate) > 8:
            parts = [part for part in SPLIT_CHINESE.split(candidate) if part]
            if not parts:
                parts = [candidate]
        for part in parts:
            value = part.casefold().strip("._-")
            if len(value) < 2 or value in STOPWORDS:
                continue
            if value not in keywords:
                keywords.append(value)
    return keywords[:20]


class ContextFinder:
    def __init__(
        self,
        root: Path,
        task: str,
        keywords: list[str] | None = None,
        anchors: list[str] | None = None,
        max_results: int = 10,
        max_file_bytes: int = 262144,
        max_total_bytes: int = 8388608,
        use_cache: bool = True,
        cache_path: Path | None = None,
        focus: bool = True,
    ):
        if max_results < 1 or max_file_bytes < 1 or max_total_bytes < 1:
            raise ConfigError("结果数和字节预算必须为正整数")
        self.task = task
        self.keywords = derive_keywords(task, keywords or [])
        self.anchors = [normalize_project_path(value) for value in anchors or []]
        self.max_results = max_results
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes
        self.use_cache = use_cache
        self.focus = focus
        self.checker = ForestChecker(root)
        self.root = self.checker.root
        self.cache_path = cache_path or self.root / ".cache" / "agent-workflow" / "context.sqlite"
        if self.use_cache and self.cache_path.is_file():
            try:
                info_cache = ContextCache.load_info(self.cache_path)
                self.checker.info_cache = info_cache
                self.checker.expected = set(info_cache)
            except (sqlite3.Error, json.JSONDecodeError):
                pass
        self.expected = self.checker.expected
        self.module_metadata_cache = {}
        self.total_bytes = 0
        self.content_files = 0
        self.source_reads = 0
        self.skipped_large = 0
        self.skipped_binary = 0
        self.truncated = False

    def scan_files(self) -> list[tuple[str, Path, str]]:
        result = []
        for relative in sorted(self.expected):
            directory = self.checker.source_path(relative)
            for entry in sorted(os.scandir(directory), key=lambda item: item.name):
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    continue
                path = Path(entry.path)
                file_relative = entry.name if relative == "." else f"{relative}/{entry.name}"
                if self.checker.is_excluded(file_relative):
                    continue
                result.append((file_relative, path, relative))
        return result

    def files(self) -> list[tuple[str, Path, str]]:
        try:
            return self.scan_files()
        except FileNotFoundError:
            files, _ = self.refresh_sources()
            return files

    def refresh_sources(self) -> tuple[list[tuple[str, Path, str]], dict[str, Any]]:
        self.checker = ForestChecker(self.root)
        self.module_metadata_cache.clear()
        self.checker.enumerate_expected()
        self.expected = self.checker.expected
        return self.scan_files(), self.checker.info_cache

    def module_metadata(self, relative: str) -> tuple[str, dict[str, Any]]:
        if relative in self.module_metadata_cache:
            return self.module_metadata_cache[relative]
        current = relative
        while True:
            data = self.checker.info_cache.get(current)
            if isinstance(data, dict) and any(
                key in data for key in ("boundary", "entry", "depends_on")
            ):
                result = (current, data)
                self.module_metadata_cache[relative] = result
                return result
            if current == ".":
                data = self.checker.info_cache.get(relative)
                result = (relative, data if isinstance(data, dict) else {})
                self.module_metadata_cache[relative] = result
                return result
            current = PurePosixPath(current).parent.as_posix()

    def descriptions(self, relative: str, name: str) -> tuple[str, str]:
        data = self.checker.info_cache.get(relative)
        if not isinstance(data, dict):
            return "", ""
        module_relative, module_data = self.module_metadata(relative)
        directory_text = " ".join(
            [
                str(data.get("purpose", "")),
                str(data.get("contents_summary", "")),
                str(module_data.get("purpose", "")),
                str(module_data.get("contents_summary", "")),
                module_text(module_data, module_relative),
            ]
        ).casefold()
        file_parts = []
        for entry in data.get("files", []):
            if isinstance(entry, dict) and entry.get("name") == name:
                file_parts.extend([
                    str(entry.get("purpose", "")),
                    str(entry.get("details", "")),
                    module_text(entry, relative),
                ])
        return directory_text, " ".join(file_parts).casefold()

    def read_text(self, path: Path) -> str:
        size = path.stat().st_size
        if size > self.max_file_bytes:
            self.skipped_large += 1
            return ""
        if self.total_bytes + size > self.max_total_bytes:
            self.truncated = True
            return ""
        try:
            raw = path.read_bytes()
        except OSError:
            return ""
        if b"\0" in raw:
            self.skipped_binary += 1
            return ""
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            self.skipped_binary += 1
            return ""
        self.total_bytes += size
        self.source_reads += 1
        return text

    def text_matches(self, content: str) -> tuple[dict[str, int], list[dict[str, Any]]]:
        if not content:
            return {}, []
        self.content_files += 1
        counts: dict[str, int] = defaultdict(int)
        lines = []
        for number, line in enumerate(content.splitlines(), 1):
            folded = line.casefold()
            matched = [keyword for keyword in self.keywords if keyword in folded]
            for keyword in matched:
                counts[keyword] += 1
            if matched and len(lines) < 5:
                lines.append({"line": number, "keywords": matched})
        return counts, lines

    def evaluate(self, record: dict[str, Any]) -> dict[str, Any] | None:
        relative = record["path"]
        parent = record["parent"]
        folded_path = relative.casefold()
        directory_description = record["directory_description"]
        file_description = record["file_description"]
        matched = {
            keyword for keyword in self.keywords
            if keyword in folded_path or keyword in file_description
        }
        counts, lines = self.text_matches(record["content"])
        matched.update(counts)
        anchored = any(
            relative == anchor or relative.startswith(f"{anchor}/")
            for anchor in self.anchors
        )
        if not matched and not anchored:
            return None
        score = 1000 if anchored else 0
        reasons = ["anchor"] if anchored else []
        for keyword in matched:
            if keyword in folded_path:
                score += 50
                reasons.append("path")
            if keyword in file_description:
                score += 40
                reasons.append("summary")
            if keyword in directory_description:
                score += 10
                reasons.append("directory")
            if keyword in counts:
                score += 10 + min(counts[keyword], 3)
                reasons.append("content")
        return {
            "path": relative,
            "score": score,
            "matches": sorted(matched),
            "reasons": sorted(set(reasons)),
            "lines": lines,
            "role": record["role"],
        }

    def content_prefixes(self, all_files: list[tuple[str, Path, str]]) -> list[str]:
        if not self.focus:
            return ["."]
        candidates = []
        for relative, _, parent in all_files:
            directory_description, file_description = self.descriptions(parent, PurePosixPath(relative).name)
            folded_path = relative.casefold()
            matched = {
                keyword for keyword in self.keywords
                if keyword in folded_path or keyword in file_description
            }
            anchored = any(
                relative == anchor or relative.startswith(f"{anchor}/")
                for anchor in self.anchors
            )
            if not matched and not anchored:
                continue
            score = 1000 if anchored else 0
            for keyword in matched:
                score += 50 if keyword in folded_path else 0
                score += 40 if keyword in file_description else 0
                score += 10 if keyword in directory_description else 0
            candidates.append((score, relative))
        if not candidates:
            return ["."]
        candidates.sort(key=lambda item: (-item[0], item[1]))
        focus = self.module_prefix(candidates[0][1])
        data = self.checker.info_cache.get(focus)
        if not isinstance(data, dict) or not any(
            key in data for key in ("boundary", "entry", "depends_on")
        ):
            return ["."]
        prefixes = [focus]
        for test_root in ("tests", "test"):
            if (self.root / test_root).is_dir():
                prefixes.append(test_root)
        return prefixes

    def select_focus(self, candidates: list[dict[str, Any]]) -> tuple[str | None, bool]:
        if not candidates:
            return None, False
        by_module = {}
        for item in candidates:
            by_module.setdefault(self.module_prefix(item["path"]), []).append(item)
        keyword_modules = {}
        for module, items in by_module.items():
            for keyword in {keyword for item in items for keyword in item["matches"]}:
                keyword_modules.setdefault(keyword, set()).add(module)
        scores = {}
        for module, items in by_module.items():
            data = self.checker.info_cache.get(module)
            if not isinstance(data, dict) or not any(
                key in data for key in ("boundary", "entry", "depends_on")
            ):
                continue
            score = 0.0
            for keyword in {keyword for item in items for keyword in item["matches"]}:
                specificity = 1 / len(keyword_modules[keyword])
                reasons = {
                    reason for item in items if keyword in item["matches"]
                    for reason in item["reasons"]
                }
                score += 25 * specificity
                for reason, points in (("anchor", 1000), ("path", 50), ("summary", 35), ("directory", 10), ("content", 5)):
                    if reason in reasons:
                        score += points * specificity
            scores[module] = score
        if not scores:
            return None, False
        top_overall = self.module_prefix(candidates[0]["path"])
        if top_overall not in scores:
            return None, False
        ordered = sorted(scores, key=lambda module: (-scores[module], module))
        top = ordered[0]
        ambiguous = len(ordered) > 1 and scores[ordered[1]] >= scores[top] * 0.9
        return (None, True) if ambiguous else (top, False)

    def run(self) -> dict[str, Any]:
        if not self.expected:
            self.refresh_sources()
        all_files = self.files()
        cache_report = {"enabled": self.use_cache, "status": "disabled", "path": None}
        if self.use_cache:
            cache_path = self.cache_path
            cache = ContextCache(
                self.root,
                cache_path,
                all_files,
                self.descriptions,
                self.max_file_bytes,
                self.max_total_bytes,
                self.checker.info_cache,
                self.refresh_sources,
                self.content_prefixes(all_files),
            )
            all_files = cache.files
            self.expected = set(cache.info_data)
            records = cache.query(self.keywords, self.anchors)
            cache_report = {
                "enabled": True,
                "status": cache.status,
                "path": str(cache_path),
            }
            self.source_reads = cache.source_reads
            self.skipped_large = cache.skipped_large
            self.skipped_binary = cache.skipped_binary
            self.truncated = cache.truncated
            cache.close()
        else:
            records = []
            for relative, path, parent in all_files:
                directory_description, file_description = self.descriptions(parent, path.name)
                records.append({
                    "path": relative,
                    "parent": parent,
                    "role": "test" if self.is_test(relative) else "source",
                    "directory_description": directory_description,
                    "file_description": file_description,
                    "content": self.read_text(path),
                })
        candidates = [candidate for record in records if (candidate := self.evaluate(record))]
        candidates.sort(key=lambda item: (-item["score"], item["path"]))
        all_candidates = candidates
        focus_module = None
        focus_ambiguous = False
        focus_filtered_out = 0
        if self.focus and candidates:
            focus_module, focus_ambiguous = self.select_focus(candidates)
            if focus_module:
                candidates = [
                    item for item in candidates
                    if self.module_prefix(item["path"]) == focus_module
                ]
                focus_filtered_out = len(all_candidates) - len(candidates)
        selected = candidates[: self.max_results]
        related = self.related_tests(selected, all_candidates, all_files)
        related_modules = self.related_modules(selected)
        if focus_module:
            related_modules = self.add_dependent_modules(focus_module, related_modules)
        core_members = self.core_members(focus_module, selected, related_modules)
        navigation = self.navigation(selected + related + core_members)
        return {
            "schema_version": "1.0",
            "task": self.task,
            "keywords": self.keywords,
            "focus_module": focus_module,
            "focus_ambiguous": focus_ambiguous,
            "locations": selected,
            "core_members": core_members,
            "related_tests": related,
            "related_modules": related_modules,
            "navigation": navigation,
            "cache": cache_report,
            "scan": {
                "directories": len(self.expected),
                "files": len(all_files),
                "content_files": self.content_files,
                "source_reads": self.source_reads,
                "skipped_large_files": self.skipped_large,
                "skipped_binary_files": self.skipped_binary,
                "truncated": self.truncated,
                "candidate_count": len(all_candidates),
                "focus_filtered_out": focus_filtered_out,
                "omitted_candidates": max(0, len(candidates) - len(selected)),
            },
            "info_errors": sorted(self.checker.info_errors),
            "limitations": [
                "关键词与模块关系用于定位，不证明语义覆盖完整",
                "核心成员是维护的架构标记，修改前仍须核验源码与测试",
            ],
        }

    def related_modules(self, selected: list[dict[str, Any]]) -> list[dict[str, str]]:
        related = []
        seen = set()
        for item in selected:
            relative = item["path"]
            parent = PurePosixPath(relative).parent.as_posix()
            data = self.checker.info_cache.get(parent)
            if not isinstance(data, dict):
                continue
            module_relative, module_data = self.module_metadata(parent)
            owners = [(module_relative, module_data, module_relative)]
            for entry in data.get("files", []):
                if isinstance(entry, dict) and entry.get("name") == PurePosixPath(relative).name:
                    owners.append((relative, entry, parent))
            for source, owner, source_directory in owners:
                for dependency in module_dependencies(owner, source_directory):
                    identity = (source, dependency["target"], dependency["kind"])
                    if identity in seen:
                        continue
                    seen.add(identity)
                    related.append({"source": source, **dependency})
        return related[:5]

    def add_dependent_modules(
        self,
        focus: str,
        related: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        result = list(related)
        seen = {(item["source"], item["target"], item["kind"]) for item in result}
        for relative in sorted(self.expected):
            data = self.checker.info_cache.get(relative)
            if not isinstance(data, dict):
                continue
            for dependency in module_dependencies(data, relative):
                if dependency["target"] != focus:
                    continue
                identity = (relative, dependency["target"], dependency["kind"])
                if identity not in seen:
                    result.append({"source": relative, **dependency})
                    seen.add(identity)
        return result[:8]

    def core_members(
        self,
        focus: str | None,
        selected: list[dict[str, Any]],
        related_modules: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        modules = set()
        if focus:
            modules.add(focus)
        for item in selected:
            modules.add(self.module_prefix(item["path"]))
        for item in related_modules:
            for value in (item["source"], item["target"]):
                path = self.checker.source_path(value)
                modules.add(value if path.is_dir() else PurePosixPath(value).parent.as_posix())
        result = []
        for relative in sorted(self.expected):
            if modules and not any(
                relative == module or relative.startswith(f"{module}/") for module in modules
            ):
                continue
            data = self.checker.info_cache.get(relative)
            if not isinstance(data, dict):
                continue
            for entry in data.get("files", []):
                if not isinstance(entry, dict) or entry.get("core") is not True:
                    continue
                path = entry["name"] if relative == "." else f"{relative}/{entry['name']}"
                result.append({
                    "path": path,
                    "purpose": entry["purpose"],
                    "source_module": self.module_prefix(path),
                })
        result.sort(key=lambda item: item["path"])
        return result

    @staticmethod
    def is_test(relative: str) -> bool:
        path = PurePosixPath(relative)
        return bool({"test", "tests"} & set(path.parts)) or path.name.startswith("test_")

    @staticmethod
    def module_prefix(relative: str) -> str:
        parts = PurePosixPath(relative).parts
        if parts and parts[0] == "Libraries" and len(parts) > 1:
            return "/".join(parts[:2])
        return parts[0] if parts else "."

    def related_tests(
        self,
        selected: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
        all_files: list[tuple[str, Path, str]],
    ) -> list[dict[str, Any]]:
        selected_paths = {item["path"] for item in selected}
        test_candidates = {item["path"]: item for item in candidates if item["role"] == "test"}
        related = []
        for item in selected:
            if item["role"] == "test":
                continue
            stem = PurePosixPath(item["path"]).stem
            expected_names = {f"test_{stem}", f"{stem}_test"}
            for relative, _, _ in all_files:
                if relative in selected_paths or not self.is_test(relative):
                    continue
                if PurePosixPath(relative).stem not in expected_names:
                    continue
                test = test_candidates.get(relative, {
                    "path": relative,
                    "score": item["score"],
                    "matches": [],
                    "reasons": [],
                    "lines": [],
                    "role": "test",
                })
                test = dict(test)
                test["reasons"] = sorted(set(test["reasons"] + ["related_test"]))
                related.append(test)
                selected_paths.add(relative)
                if len(related) >= 3:
                    return related
        extras = sorted(
            (
                item for item in test_candidates.values()
                if item["path"] not in selected_paths and item["matches"]
            ),
            key=lambda item: (-item["score"], item["path"]),
        )
        for extra in extras:
            for source in selected:
                if source["role"] == "test":
                    continue
                same_module = self.module_prefix(extra["path"]) == self.module_prefix(source["path"])
                mirrored_tests = (
                    extra["path"].startswith(("tests/", "test/"))
                    and source["path"].startswith("src/")
                )
                if not same_module and not mirrored_tests:
                    continue
                extra = dict(extra)
                extra["reasons"] = sorted(set(extra["reasons"] + ["keyword_test"]))
                related.append(extra)
                selected_paths.add(extra["path"])
                break
            if len(related) >= 3:
                break
        return related

    def navigation(self, locations: list[dict[str, Any]]) -> list[str]:
        paths = []
        for item in locations:
            parent = PurePosixPath(item["path"]).parent.as_posix()
            if parent not in self.expected or parent in self.checker.info_errors:
                continue
            info = self.checker.info_cache.get(parent)
            if not isinstance(info, dict):
                continue
            path = ".doc-forest/info.yml" if parent == "." else f".doc-forest/{parent}/info.yml"
            if path not in paths:
                paths.append(path)
        return paths


def find_context(root: Path, task: str, **options: Any) -> dict[str, Any]:
    return ContextFinder(root, task, **options).run()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--task", required=True, help="当前需求或任务描述")
    parser.add_argument("--keyword", action="append", default=[], help="精确关键词，可重复")
    parser.add_argument("--path", action="append", default=[], help="已知路径锚点，可重复")
    parser.add_argument("--max-results", type=int, default=10)
    parser.add_argument("--max-file-bytes", type=int, default=262144)
    parser.add_argument("--max-total-bytes", type=int, default=8388608)
    parser.add_argument("--cache", type=Path, help="派生 SQLite 缓存路径")
    parser.add_argument("--no-cache", action="store_true", help="直接读取源码，不写缓存")
    parser.add_argument("--no-focus", action="store_true", help="不按最高证据模块聚焦")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    arguments = parser.parse_args()
    try:
        report = find_context(
            arguments.root,
            arguments.task,
            keywords=arguments.keyword,
            anchors=arguments.path,
            max_results=arguments.max_results,
            max_file_bytes=arguments.max_file_bytes,
            max_total_bytes=arguments.max_total_bytes,
            use_cache=not arguments.no_cache,
            cache_path=arguments.cache,
            focus=not arguments.no_focus,
        )
    except (ConfigError, OSError, sqlite3.Error) as exc:
        if arguments.format == "json":
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if arguments.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print(f"KEYWORDS: {', '.join(report['keywords']) or '(none)'}")
    if report["focus_module"]:
        print(f"FOCUS: {report['focus_module']}")
    elif report["focus_ambiguous"]:
        print("FOCUS: ambiguous; no module filter applied")
    for item in report["locations"]:
        print(f"{item['path']}  score={item['score']}  reasons={','.join(item['reasons'])}")
        for line in item["lines"]:
            print(f"  line {line['line']}: {','.join(line['keywords'])}")
    for item in report["core_members"]:
        print(f"CORE: {item['path']}  {item['purpose']}")
    for item in report["related_tests"]:
        print(f"RELATED TEST: {item['path']}")
    for item in report["related_modules"]:
        print(
            f"RELATED MODULE: {item['source']} -> {item['target']}  "
            f"[{item['kind']}] {item['reason']}"
        )
    for path in report["navigation"]:
        print(f"NAV: {path}")
    scan = report["scan"]
    print(
        f"SCAN: {scan['files']} files, source reads={scan['source_reads']}, "
        f"cache={report['cache']['status']}, {scan['omitted_candidates']} omitted, "
        f"focus filtered={scan['focus_filtered_out']}, truncated={scan['truncated']}"
    )
    if not report["locations"]:
        print("No matching locations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
