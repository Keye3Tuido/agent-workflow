#!/usr/bin/env python3
"""Disposable SQLite cache for task-context lookups; source files stay authoritative."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Callable

from yaml_common import file_lock


CACHE_VERSION = "3"


class ContextCache:
    def __init__(
        self,
        root: Path,
        cache_path: Path,
        files: list[tuple[str, Path, str]],
        description: Callable[[str, str], tuple[str, str]],
        max_file_bytes: int,
        max_total_bytes: int,
        info_data: dict[str, Any],
        refresh: Callable[[], tuple[list[tuple[str, Path, str]], dict[str, Any]]],
        content_prefixes: list[str],
    ):
        self.root = root
        self.cache_path = cache_path
        self.source_reads = 0
        self.skipped_large = 0
        self.skipped_binary = 0
        self.truncated = False
        self.total_bytes = 0
        self.status = "hit"
        self.files = files
        self.description = description
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes
        self.info_data = info_data
        self.refresh = refresh
        self.content_prefixes = set(content_prefixes)
        with file_lock(cache_path):
            signature = self.signature()
            if not self.matches(signature):
                self.status = "built" if not cache_path.exists() else "rebuilt"
                self.files, self.info_data = self.refresh()
                self.content_prefixes.update(self.read_scopes())
                signature = self.signature()
                self.build(signature)
        self.connection = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)
        self.connection.row_factory = sqlite3.Row
        self.ensure_scopes(self.content_prefixes)

    def signature(self) -> str:
        records = []

        def add(relative: str, path: Path, kind: str = "file") -> None:
            try:
                stat = path.stat()
                records.append((kind, relative, stat.st_size, stat.st_mtime_ns))
            except OSError:
                records.append((kind, relative, -1, -1))

        for relative, path, _ in self.files:
            add(relative, path)
        for relative in self.info_data:
            add(relative, self.root / relative, "directory")
        forest = self.root / ".doc-forest"
        if forest.is_dir():
            for current, directory_names, file_names in os.walk(forest, followlinks=False):
                directory_names[:] = sorted(directory_names)
                for name in sorted(file_names):
                    if name == "info.yml":
                        path = Path(current) / name
                        add(str(path.relative_to(self.root)), path, "info")
        config = self.root / ".doc-forest.yml"
        if config.is_file():
            add(".doc-forest.yml", config, "config")
        payload = json.dumps(
            {
                "files": records,
                "max_file_bytes": self.max_file_bytes,
                "max_total_bytes": self.max_total_bytes,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def matches(self, signature: str) -> bool:
        if not self.cache_path.is_file():
            return False
        try:
            connection = sqlite3.connect(f"file:{self.cache_path}?mode=ro", uri=True)
            row = connection.execute("SELECT value FROM meta WHERE key = 'signature'").fetchone()
            version = connection.execute("SELECT value FROM meta WHERE key = 'version'").fetchone()
            connection.close()
            return bool(row and row[0] == signature and version and version[0] == CACHE_VERSION)
        except sqlite3.DatabaseError:
            return False

    def build(self, signature: str) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_name = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.cache_path.parent,
                prefix=".context-",
                suffix=".sqlite",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
            connection = sqlite3.connect(temporary_name)
            connection.execute("PRAGMA journal_mode=OFF")
            connection.execute("PRAGMA synchronous=OFF")
            connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute(
                """
                CREATE TABLE files (
                    path TEXT PRIMARY KEY,
                    parent TEXT NOT NULL,
                    role TEXT NOT NULL,
                    directory_description TEXT NOT NULL,
                    file_description TEXT NOT NULL,
                    content TEXT NOT NULL
                )
                """
            )
            connection.execute("CREATE TABLE info (relative TEXT PRIMARY KEY, data_json TEXT NOT NULL)")
            connection.execute("CREATE TABLE scopes (prefix TEXT PRIMARY KEY)")
            rows = []
            for relative, path, parent in self.files:
                directory_description, file_description = self.description(parent, path.name)
                role = "test" if self.is_test(relative) else "source"
                content = self.read_text(path) if self.in_scope(relative) else ""
                rows.append((relative, parent, role, directory_description, file_description, content))
            connection.executemany("INSERT INTO files VALUES (?, ?, ?, ?, ?, ?)", rows)
            connection.executemany(
                "INSERT INTO scopes VALUES (?)",
                [(prefix,) for prefix in sorted(self.content_prefixes)],
            )
            connection.executemany(
                "INSERT INTO info VALUES (?, ?)",
                [
                    (relative, json.dumps(data, ensure_ascii=False, separators=(",", ":")))
                    for relative, data in self.info_data.items()
                ],
            )
            connection.executemany(
                "INSERT INTO meta VALUES (?, ?)",
                [("signature", signature), ("version", CACHE_VERSION)],
            )
            connection.commit()
            connection.close()
            os.replace(temporary_name, self.cache_path)
            temporary_name = None
        finally:
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass

    def in_scope(self, relative: str, prefixes: set[str] | None = None) -> bool:
        prefixes = self.content_prefixes if prefixes is None else prefixes
        return any(
            prefix == "." or relative == prefix or relative.startswith(f"{prefix}/")
            for prefix in prefixes
        )

    def read_scopes(self) -> set[str]:
        if not self.cache_path.is_file():
            return set()
        try:
            connection = sqlite3.connect(f"file:{self.cache_path}?mode=ro", uri=True)
            rows = connection.execute("SELECT prefix FROM scopes").fetchall()
            connection.close()
            return {row[0] for row in rows}
        except sqlite3.DatabaseError:
            return set()

    def ensure_scopes(self, prefixes: set[str]) -> None:
        missing = set(prefixes) - self.read_scopes()
        if not missing:
            return
        self.connection.close()
        with file_lock(self.cache_path):
            connection = sqlite3.connect(self.cache_path)
            connection.execute("PRAGMA journal_mode=DELETE")
            try:
                for prefix in sorted(missing):
                    for relative, path, parent in self.files:
                        if not self.in_scope(relative, {prefix}):
                            continue
                        content = self.read_text(path)
                        directory_description, file_description = self.description(parent, path.name)
                        connection.execute(
                            "UPDATE files SET content = ? WHERE path = ?",
                            (content, relative),
                        )
                    connection.execute("INSERT OR IGNORE INTO scopes VALUES (?)", (prefix,))
                connection.commit()
            finally:
                connection.close()
        self.status = "extended"
        self.connection = sqlite3.connect(f"file:{self.cache_path}?mode=ro", uri=True)
        self.connection.row_factory = sqlite3.Row

    @staticmethod
    def is_test(relative: str) -> bool:
        from pathlib import PurePosixPath

        path = PurePosixPath(relative)
        return bool({"test", "tests"} & set(path.parts)) or path.name.startswith("test_")

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

    def query(self, keywords: list[str], anchors: list[str]) -> list[dict[str, Any]]:
        self.connection.create_function("casefold", 1, lambda value: value.casefold())
        conditions = []
        parameters = []
        for keyword in keywords:
            for column in ("path", "file_description", "directory_description", "content"):
                conditions.append(f"instr(casefold({column}), ?) > 0")
                parameters.append(keyword)
        for anchor in anchors:
            conditions.append("path = ?")
            parameters.append(anchor)
            conditions.append("substr(path, 1, ?) = ?")
            parameters.extend([len(anchor) + 1, anchor + "/"])
        sql = "SELECT * FROM files"
        if conditions:
            sql += " WHERE " + " OR ".join(conditions)
        rows = self.connection.execute(sql, parameters).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def load_info(cache_path: Path) -> dict[str, Any]:
        connection = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)
        try:
            rows = connection.execute("SELECT relative, data_json FROM info").fetchall()
            return {relative: json.loads(data_json) for relative, data_json in rows}
        finally:
            connection.close()

    def close(self) -> None:
        self.connection.close()
